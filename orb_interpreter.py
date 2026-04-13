"""
ORB LANGUAGE — INTERPRETER
============================
Executes the AST produced by the parser.

Design principles:
  - Source is ALWAYS retained at runtime (AST is the live state)
  - HALT freezes execution; state remains inspectable
  - INSPECT dumps live state (for geometric view hookup)
  - Modules have independent state; communication via ports/messages/shared
  - BBC BASIC-style inline asm with variable bridging
  - Expression immediates evaluated at Orb level, injected as values
  - Two-pass assembly via OPT (pass 0 = collect labels, pass 2 = execute)

Execution phases:
  Phase 1: DEFINE — parse all MODULE definitions into a type registry
  Phase 2: PLACE — instantiate modules from a BOARD declaration
  Phase 3: WIRE/ROUTE/SHARE — build the connection graph
  Phase 4: EXECUTE — topological sort, run modules in dependency order
"""

from typing import Dict, List, Optional, Any, Tuple, Callable
from dataclasses import dataclass, field
from collections import deque
import copy
import math

from orb_ast import *
from orb_lexer import Lexer, TokenType
from orb_parser import Parser, parse_source


# ============================================================
#  RUNTIME VALUES
# ============================================================

@dataclass
class OrbVec:
    """Vector value — fixed-width array of floats."""
    data: List[float] = field(default_factory=list)
    width: int = 0
    
    def __repr__(self):
        return f"<{', '.join(f'{v:.4g}' for v in self.data)}>"
    
    def copy(self):
        return OrbVec(data=list(self.data), width=self.width)


def make_default(type_name: str, vec_width: Optional[int] = None) -> Any:
    """Create a default value for a given type."""
    if type_name == "INT":
        return 0
    elif type_name == "FLOAT":
        return 0.0
    elif type_name == "STRING":
        return ""
    elif type_name == "VEC":
        w = vec_width or 4
        return OrbVec(data=[0.0] * w, width=w)
    return 0


# ============================================================
#  ENVIRONMENT (SCOPE)
# ============================================================

class Environment:
    """
    Variable scope with parent chain.
    Each module instance gets its own environment.
    """
    
    def __init__(self, parent=None, name: str = "<global>"):
        self.parent = parent
        self.name = name
        self.vars: Dict[str, Any] = {}
        self.constants: Dict[str, Any] = {}
        self.labels: Dict[str, int] = {}     # @label → statement index
        self.gosub_stack: List[int] = []
    
    def get(self, name: str) -> Any:
        if name in self.vars:
            return self.vars[name]
        if name in self.constants:
            return self.constants[name]
        if self.parent:
            return self.parent.get(name)
        raise OrbRuntimeError(f"Undefined variable: {name}")
    
    def set(self, name: str, value: Any):
        if name in self.constants:
            raise OrbRuntimeError(f"Cannot assign to constant: {name}")
        # Walk up scope chain
        env = self
        while env:
            if name in env.vars:
                env.vars[name] = value
                return
            env = env.parent
        # New variable in current scope
        self.vars[name] = value
    
    def define(self, name: str, value: Any):
        """Define a new variable in the current scope."""
        self.vars[name] = value
    
    def define_const(self, name: str, value: Any):
        self.constants[name] = value
    
    def dump(self) -> Dict[str, Any]:
        """Dump all variables for INSPECT."""
        result = {}
        if self.parent:
            result.update(self.parent.dump())
        result.update(self.vars)
        result.update({f"CONST {k}": v for k, v in self.constants.items()})
        return result


# ============================================================
#  PORT & MESSAGE INFRASTRUCTURE
# ============================================================

@dataclass
class Port:
    """A typed port buffer on a module instance."""
    name: str
    direction: str       # "IN" | "OUT" | "INOUT"
    type_name: str
    vec_width: Optional[int] = None
    value: Any = None
    connected: bool = False
    
    def __post_init__(self):
        if self.value is None:
            self.value = make_default(self.type_name, self.vec_width)


@dataclass
class MessageQueue:
    """Message channel between module instances."""
    queue: deque = field(default_factory=deque)
    max_size: int = 256
    
    def send(self, value: Any):
        if len(self.queue) >= self.max_size:
            self.queue.popleft()
        self.queue.append(value)
    
    def recv(self, timeout: int = 0) -> Optional[Any]:
        if self.queue:
            return self.queue.popleft()
        return None
    
    @property
    def has_message(self) -> bool:
        return len(self.queue) > 0


@dataclass
class SharedState:
    """Shared state with lock semantics."""
    name: str
    value: Any = None
    locked_by: Optional[str] = None  # Instance name holding the lock


# ============================================================
#  MODULE INSTANCE
# ============================================================

@dataclass
class ModuleInstance:
    """A placed, wired instance of a module."""
    instance_name: str
    module_type: str
    comm_mode: str
    definition: ModuleDecl
    env: Environment = field(default_factory=lambda: Environment())
    in_ports: Dict[str, Port] = field(default_factory=dict)
    out_ports: Dict[str, Port] = field(default_factory=dict)
    message_channels: Dict[str, MessageQueue] = field(default_factory=dict)
    executed: bool = False
    halted: bool = False
    halt_message: Optional[str] = None


# ============================================================
#  REGISTER FILE (for ASM blocks)
# ============================================================

class RegisterFile:
    """
    orb42_core_v2 register file:
      D0-D7: 8 data registers (D0 conventionally zero'd at boot)
      A0-A7: 8 address registers (A7 = stack pointer)
      DP:    data page register (for LOAD.DP / STORE.DP)
      FLAGS: Z (zero), N (negative), C (carry)
    """
    
    def __init__(self):
        self.d = [0] * 8       # Data registers
        self.a = [0] * 8       # Address registers
        self.dp = 0x00050000   # Data page (default = phaser bank)
        self.flags = {"Z": False, "N": False, "C": False}
        self.a[7] = 0x0000FFF0  # Stack pointer
    
    def get(self, name: str) -> Any:
        """Read a register by name (D0-D7, A0-A7, DP)."""
        upper = name.upper()
        if upper == "DP":
            return self.dp
        if len(upper) == 2:
            idx = int(upper[1])
            if upper[0] == 'D':
                return self.d[idx]
            elif upper[0] == 'A':
                return self.a[idx]
        raise OrbRuntimeError(f"Unknown register: {name}")
    
    def set(self, name: str, value: Any):
        """Write a register by name."""
        upper = name.upper()
        if upper == "DP":
            self.dp = int(value) if isinstance(value, (int, float)) else value
            return
        if len(upper) == 2:
            idx = int(upper[1])
            if upper[0] == 'D':
                self.d[idx] = value
                return
            elif upper[0] == 'A':
                self.a[idx] = value
                return
        raise OrbRuntimeError(f"Unknown register: {name}")
    
    def update_flags(self, result: Any):
        if isinstance(result, (int, float)):
            self.flags["Z"] = (result == 0)
            self.flags["N"] = (result < 0)
        else:
            self.flags["Z"] = False
            self.flags["N"] = False
    
    def dump(self) -> Dict[str, Any]:
        result = {}
        for i in range(8):
            if self.d[i] != 0:
                result[f"D{i}"] = self.d[i]
        for i in range(8):
            if self.a[i] != 0:
                result[f"A{i}"] = self.a[i]
        if self.dp != 0:
            result["DP"] = hex(self.dp)
        result["FLAGS"] = dict(self.flags)
        return result


# ============================================================
#  ERRORS
# ============================================================

class OrbRuntimeError(Exception):
    def __init__(self, message: str, node: Optional[ASTNode] = None):
        self.node = node
        loc = f" at L{node.line}:{node.col}" if node else ""
        super().__init__(f"Runtime error{loc}: {message}")


class OrbHalt(Exception):
    """Raised when HALT is executed — not an error, a deliberate stop."""
    def __init__(self, message: str = ""):
        self.message = message
        super().__init__(f"HALT: {message}")


# ============================================================
#  INTERPRETER
# ============================================================

class Interpreter:
    """
    Executes an Orb AST.
    
    Usage:
        interp = Interpreter()
        interp.run(ast)           # Run a full program
        interp.run_source(code)   # Lex + parse + run
    """
    
    def __init__(self, output_fn: Optional[Callable] = None):
        # Module type registry (definitions)
        self.module_types: Dict[str, ModuleDecl] = {}
        # Board definitions
        self.boards: Dict[str, BoardDecl] = {}
        # Live instances
        self.instances: Dict[str, ModuleInstance] = {}
        # Shared state objects
        self.shared_states: Dict[str, SharedState] = {}
        # Wire connections (src_instance.port → dst_instance.port)
        self.wires: List[Tuple[str, str, str, str]] = []
        # Message routes
        self.routes: List[Tuple[str, str, str, str]] = []
        # Global environment
        self.global_env = Environment(name="<global>")
        # Register file for ASM
        self.registers = RegisterFile()
        # ASM label tables (for two-pass assembly)
        self.asm_labels: Dict[str, Any] = {}
        # Output function (PRINT target)
        self.output_fn = output_fn or (lambda *args: print(*args))
        # Execution trace for geometric view
        self.trace: List[Dict] = []
        # INSPECT output
        self.inspect_log: List[Dict] = []
        # Halted state
        self.halted = False
        self.halt_message = ""
    
    # ============================================================
    #  TOP LEVEL
    # ============================================================
    
    def run(self, program: Program):
        """Execute a full program AST."""
        # Phase 1: DEFINE — register all module and board types
        for node in program.body:
            if isinstance(node, ModuleDecl):
                self.module_types[node.name] = node
            elif isinstance(node, BoardDecl):
                self.boards[node.name] = node
        
        # Phase 2-4: Execute boards (or run top-level statements)
        for node in program.body:
            if isinstance(node, BoardDecl):
                self.execute_board(node)
            elif isinstance(node, ImportStmt):
                pass  # TODO: file loading
            elif isinstance(node, (ModuleDecl, CommentNode)):
                pass  # Already registered / skip
            else:
                # Top-level statement
                self.exec_statement(node, self.global_env)
    
    def run_source(self, source: str, filename: str = "<input>"):
        """Lex, parse, and execute source code."""
        program = parse_source(source, filename)
        self.run(program)
    
    # ============================================================
    #  BOARD EXECUTION
    # ============================================================
    
    def execute_board(self, board: BoardDecl):
        """Execute a board: place instances, wire them, run."""
        # Phase 2: PLACE
        for node in board.body:
            if isinstance(node, PlaceStmt):
                self.place_instance(node)
        
        # Phase 3: WIRE / ROUTE / SHARE / SET
        for node in board.body:
            if isinstance(node, WireStmt):
                self.connect_wire(node)
            elif isinstance(node, RouteStmt):
                self.connect_route(node)
            elif isinstance(node, ShareStmt):
                self.create_shared(node)
            elif isinstance(node, SetStmt):
                self.apply_set(node)
        
        # Phase 4: EXECUTE — topological sort and run
        self.execute_instances()
    
    def place_instance(self, node: PlaceStmt):
        """Instantiate a module."""
        mod_def = self.module_types.get(node.module_type)
        if not mod_def:
            raise OrbRuntimeError(
                f"Unknown module type: {node.module_type}", node
            )
        
        env = Environment(parent=self.global_env,
                          name=node.instance_name)
        
        inst = ModuleInstance(
            instance_name=node.instance_name,
            module_type=node.module_type,
            comm_mode=mod_def.comm_mode,
            definition=mod_def,
            env=env,
        )
        
        # Create ports
        for port_decl in mod_def.ports:
            port = Port(
                name=port_decl.name,
                direction=port_decl.direction,
                type_name=port_decl.type_name,
                vec_width=port_decl.vec_width,
            )
            if port_decl.direction in ("IN", "INOUT"):
                inst.in_ports[port_decl.name] = port
                env.define(port_decl.name, port.value)
            if port_decl.direction in ("OUT", "INOUT"):
                inst.out_ports[port_decl.name] = port
                if port_decl.direction == "OUT":
                    env.define(port_decl.name, port.value)
        
        # Initialize shared block variables
        for var_node in mod_def.shared_vars:
            val = make_default(var_node.type_name, var_node.vec_width)
            if var_node.initialiser:
                val = self.eval_expr(var_node.initialiser, env)
            env.define(var_node.name, val)
        
        self.instances[node.instance_name] = inst
    
    def connect_wire(self, node: WireStmt):
        """Connect an output port to an input port."""
        self.wires.append((
            node.src_module, node.src_port,
            node.dst_module, node.dst_port
        ))
    
    def connect_route(self, node: RouteStmt):
        """Set up a message route."""
        self.routes.append((
            node.src_module, node.src_port,
            node.dst_module, node.dst_port
        ))
        # Create message queues
        src_inst = self.instances.get(node.src_module)
        dst_inst = self.instances.get(node.dst_module)
        if src_inst:
            src_inst.message_channels[node.src_port] = MessageQueue()
        if dst_inst:
            dst_inst.message_channels[node.dst_port] = MessageQueue()
    
    def create_shared(self, node: ShareStmt):
        """Create a shared state object."""
        self.shared_states[node.state_name] = SharedState(
            name=node.state_name
        )
    
    def apply_set(self, node: SetStmt):
        """Set a constant value on an instance's port."""
        inst = self.instances.get(node.module)
        if not inst:
            raise OrbRuntimeError(
                f"Unknown instance: {node.module}", node
            )
        val = self.eval_expr(node.value, self.global_env)
        
        if node.port in inst.in_ports:
            inst.in_ports[node.port].value = val
            inst.env.set(node.port, val)
        else:
            inst.env.define(node.port, val)
    
    def execute_instances(self):
        """
        Execute all placed instances in dependency order.
        Dataflow modules run in topological order.
        Message modules run after all dataflow modules.
        """
        # Build dependency graph from wires
        deps = {name: set() for name in self.instances}
        for src_mod, src_port, dst_mod, dst_port in self.wires:
            if dst_mod in deps:
                deps[dst_mod].add(src_mod)
        
        # Topological sort
        order = []
        visited = set()
        
        def visit(name):
            if name in visited:
                return
            visited.add(name)
            for dep in deps.get(name, set()):
                visit(dep)
            order.append(name)
        
        for name in self.instances:
            visit(name)
        
        # Run each instance
        for name in order:
            inst = self.instances[name]
            if inst.halted:
                continue
            
            # Sync input ports from wires
            for src_mod, src_port, dst_mod, dst_port in self.wires:
                if dst_mod == name:
                    src_inst = self.instances.get(src_mod)
                    if src_inst and src_port in src_inst.out_ports:
                        val = src_inst.out_ports[src_port].value
                        if dst_port in inst.in_ports:
                            inst.in_ports[dst_port].value = val
                            inst.env.set(dst_port, val)
            
            # Execute module body
            try:
                self.exec_body(inst.definition.body, inst.env)
                inst.executed = True
                
                # Sync output port values from env
                for port_name, port in inst.out_ports.items():
                    try:
                        port.value = inst.env.get(port_name)
                    except OrbRuntimeError:
                        pass
                        
            except OrbHalt as halt:
                inst.halted = True
                inst.halt_message = halt.message
                self.halted = True
                self.halt_message = f"[{name}] {halt.message}"
    
    # ============================================================
    #  STATEMENT EXECUTION
    # ============================================================
    
    def exec_body(self, body: List[ASTNode], env: Environment):
        """Execute a list of statements."""
        # First pass: collect labels
        for i, node in enumerate(body):
            if isinstance(node, LabelStmt):
                env.labels[node.name] = i
        
        i = 0
        while i < len(body):
            node = body[i]
            
            # Record trace
            self.trace.append({
                "node_type": type(node).__name__,
                "line": node.line,
                "col": node.col,
            })
            
            result = self.exec_statement(node, env)
            
            # Handle GOTO (returns a label name to jump to)
            if isinstance(result, str) and result.startswith("__goto:"):
                target = result[7:]
                if target in env.labels:
                    i = env.labels[target]
                    continue
                else:
                    raise OrbRuntimeError(f"Undefined label: @{target}", node)
            
            # Handle GOSUB return
            if result == "__return":
                if env.gosub_stack:
                    i = env.gosub_stack.pop()
                    continue
                return result
            
            i += 1
    
    def exec_statement(self, node: ASTNode, env: Environment) -> Optional[str]:
        """Execute a single statement. Returns jump target or None."""
        if isinstance(node, CommentNode):
            return None
        
        elif isinstance(node, VarDecl):
            val = make_default(node.type_name, node.vec_width)
            if node.initialiser:
                val = self.eval_expr(node.initialiser, env)
            env.define(node.name, val)
        
        elif isinstance(node, ConstDecl):
            val = self.eval_expr(node.value, env)
            env.define_const(node.name, val)
        
        elif isinstance(node, Assignment):
            val = self.eval_expr(node.value, env)
            if node.index:
                idx = self.eval_expr(node.index, env)
                target = env.get(node.target)
                if isinstance(target, OrbVec):
                    target.data[int(idx)] = float(val)
                elif isinstance(target, list):
                    target[int(idx)] = val
            else:
                env.set(node.target, val)
        
        elif isinstance(node, IfStmt):
            cond = self.eval_expr(node.condition, env)
            if self._truthy(cond):
                self.exec_body(node.then_body, env)
            else:
                matched = False
                for elif_c in node.elif_clauses:
                    if self._truthy(self.eval_expr(elif_c.condition, env)):
                        self.exec_body(elif_c.body, env)
                        matched = True
                        break
                if not matched and node.else_body:
                    self.exec_body(node.else_body, env)
        
        elif isinstance(node, ForStmt):
            start = self.eval_expr(node.start, env)
            end = self.eval_expr(node.end, env)
            step = self.eval_expr(node.step, env) if node.step else 1
            
            env.define(node.var_name, start)
            counter = start
            
            while (step > 0 and counter <= end) or \
                  (step < 0 and counter >= end) or \
                  (step == 0):
                env.set(node.var_name, counter)
                self.exec_body(node.body, env)
                counter += step
                if step == 0:
                    break
        
        elif isinstance(node, WhileStmt):
            while self._truthy(self.eval_expr(node.condition, env)):
                self.exec_body(node.body, env)
        
        elif isinstance(node, GotoStmt):
            return f"__goto:{node.target}"
        
        elif isinstance(node, GosubStmt):
            # Save return position (will be set by exec_body)
            env.gosub_stack.append(0)  # Placeholder
            return f"__goto:{node.target}"
        
        elif isinstance(node, ReturnStmt):
            if node.value:
                val = self.eval_expr(node.value, env)
                env.define("__return_value", val)
            return "__return"
        
        elif isinstance(node, LabelStmt):
            pass  # Labels collected in exec_body first pass
        
        elif isinstance(node, PrintStmt):
            vals = [self.eval_expr(v, env) for v in node.values]
            out_parts = []
            for v in vals:
                if isinstance(v, OrbVec):
                    out_parts.append(str(v))
                else:
                    out_parts.append(str(v))
            self.output_fn("".join(out_parts))
        
        elif isinstance(node, InputStmt):
            if node.prompt:
                self.output_fn(node.prompt)
            val = input()
            # Try to parse as number
            try:
                val = int(val)
            except ValueError:
                try:
                    val = float(val)
                except ValueError:
                    pass
            env.set(node.variable, val)
        
        elif isinstance(node, HaltStmt):
            raise OrbHalt(node.message or "")
        
        elif isinstance(node, InspectStmt):
            val = env.get(node.target)
            entry = {
                "target": node.target,
                "value": val,
                "type": type(val).__name__,
                "line": node.line,
            }
            self.inspect_log.append(entry)
            self.output_fn(f"[INSPECT {node.target}] = {val}")
        
        elif isinstance(node, Block):
            self.exec_body(node.body, env)
        
        # ---- Vector Operations ----
        elif isinstance(node, VecArith):
            self.exec_vec_arith(node, env)
        elif isinstance(node, VecLoad):
            source = self.eval_expr(node.source, env)
            if isinstance(source, OrbVec):
                env.set(node.target, source.copy())
            else:
                env.set(node.target, source)
        elif isinstance(node, VecStore):
            source = env.get(node.source)
            target_name = self.eval_expr(node.target, env)
            if isinstance(target_name, str):
                env.set(target_name, source.copy() if isinstance(source, OrbVec) else source)
            else:
                env.set(node.target.name if isinstance(node.target, Identifier) else str(node.target), source)
        elif isinstance(node, VecReduce):
            self.exec_vec_reduce(node, env)
        
        # ---- Communication ----
        elif isinstance(node, SendStmt):
            val = self.eval_expr(node.value, env)
            # Find message queue
            for src_mod, src_port, dst_mod, dst_port in self.routes:
                if dst_mod == node.target_module:
                    dst_inst = self.instances.get(dst_mod)
                    if dst_inst:
                        ch = node.target_channel or dst_port
                        if ch not in dst_inst.message_channels:
                            dst_inst.message_channels[ch] = MessageQueue()
                        dst_inst.message_channels[ch].send(val)
                    break
            else:
                # Direct send if no route — create ad hoc queue
                dst_inst = self.instances.get(node.target_module)
                if dst_inst:
                    ch = node.target_channel or "__default"
                    if ch not in dst_inst.message_channels:
                        dst_inst.message_channels[ch] = MessageQueue()
                    dst_inst.message_channels[ch].send(val)
        
        elif isinstance(node, RecvStmt):
            # Find the channel
            src_mod = node.source_module
            src_ch = node.source_channel or "__default"
            src_inst = self.instances.get(src_mod)
            val = None
            if src_inst and src_ch in src_inst.message_channels:
                val = src_inst.message_channels[src_ch].recv()
            env.set(node.variable, val if val is not None else "")
        
        elif isinstance(node, ReadPortStmt):
            val = env.get(node.port)
            env.set(node.variable, val)
        
        elif isinstance(node, LockStmt):
            # Execute body — lock semantics are conceptual in
            # single-threaded interpreter, but we track state
            shared = self.shared_states.get(node.target)
            if shared:
                shared.locked_by = env.name
            self.exec_body(node.body, env)
            if shared:
                shared.locked_by = None
        
        # ---- Inline Assembly ----
        elif isinstance(node, AsmInline):
            self.exec_asm_body(node.body, env)
        elif isinstance(node, AsmBlock):
            self.exec_asm_body(node.body, env)
        
        # ---- ASM nodes that can appear via FOR inside asm ----
        elif isinstance(node, AsmInstruction):
            self.exec_asm_instruction(node, env)
        elif isinstance(node, AsmLabelDef):
            pass  # Collected in asm pass
        elif isinstance(node, AsmOpt):
            pass  # Controls pass behavior, handled in exec_asm_body
        elif isinstance(node, AsmEquate):
            val = self.eval_expr(node.value, env)
            env.define_const(f".{node.name}", val)
            self.asm_labels[f".{node.name}"] = val
        
        # ---- Board-level (can appear at top level) ----
        elif isinstance(node, WireStmt):
            self.connect_wire(node)
        elif isinstance(node, RouteStmt):
            self.connect_route(node)
        
        # ---- Expression as statement ----
        elif isinstance(node, Expression):
            self.eval_expr(node, env)
        
        else:
            pass  # Unknown node type — skip silently
        
        return None
    
    # ============================================================
    #  VECTOR OPERATIONS
    # ============================================================
    
    def exec_vec_arith(self, node: VecArith, env: Environment):
        a = self.eval_expr(node.operand_a, env)
        b = self.eval_expr(node.operand_b, env)
        
        if not isinstance(a, OrbVec) or not isinstance(b, OrbVec):
            raise OrbRuntimeError(
                f"Vector operation requires VEC operands, got "
                f"{type(a).__name__} and {type(b).__name__}", node
            )
        
        w = min(a.width, b.width)
        result = OrbVec(data=[0.0] * w, width=w)
        
        ops = {
            "VADD": lambda x, y: x + y,
            "VSUB": lambda x, y: x - y,
            "VMUL": lambda x, y: x * y,
            "VDIV": lambda x, y: x / y if y != 0 else 0.0,
        }
        
        if node.op == "VMADD":
            # Multiply-accumulate: a * b accumulated
            for i in range(w):
                result.data[i] = a.data[i] * b.data[i]
        elif node.op in ops:
            fn = ops[node.op]
            for i in range(w):
                result.data[i] = fn(a.data[i], b.data[i])
        
        env.set(node.target, result)
    
    def exec_vec_reduce(self, node: VecReduce, env: Environment):
        source = self.eval_expr(node.source, env)
        if not isinstance(source, OrbVec):
            raise OrbRuntimeError("VREDUCE requires VEC operand", node)
        
        if node.op == "VSUM":
            result = sum(source.data)
        elif node.op == "VDOT":
            result = sum(source.data)  # Self dot = sum of squares
        elif node.op == "VMIN":
            result = min(source.data) if source.data else 0.0
        elif node.op == "VMAX":
            result = max(source.data) if source.data else 0.0
        else:
            result = 0.0
        
        env.set(node.target, result)
    
    # ============================================================
    #  INLINE ASSEMBLY — BBC BASIC STYLE
    # ============================================================
    
    def exec_asm_body(self, body: List[ASTNode], env: Environment):
        """
        Execute an ASM block body.
        
        Handles BBC BASIC-style mixing: FOR loops, IF statements,
        and OPT directives can appear alongside asm instructions.
        Variable bridging reads/writes Orb variables through the env.
        """
        # Collect labels (first pass)
        for i, node in enumerate(body):
            if isinstance(node, AsmLabelDef):
                self.asm_labels[f".{node.name}"] = i
            elif isinstance(node, AsmEquate):
                val = self.eval_expr(node.value, env)
                env.define_const(f".{node.name}", val)
                self.asm_labels[f".{node.name}"] = val
        
        # Execute
        i = 0
        while i < len(body):
            node = body[i]
            
            if isinstance(node, AsmInstruction):
                result = self.exec_asm_instruction(node, env)
                # Handle branch results
                if isinstance(result, str) and result.startswith("__asm_jmp:"):
                    target = result[10:]
                    if target in self.asm_labels:
                        target_val = self.asm_labels[target]
                        if isinstance(target_val, int):
                            i = target_val
                            continue
                    raise OrbRuntimeError(f"Undefined asm label: {target}", node)
            elif isinstance(node, AsmLabelDef):
                pass  # Already collected
            elif isinstance(node, AsmEquate):
                pass  # Already collected
            elif isinstance(node, AsmOpt):
                pass  # Pass control — conceptual in interpreter
            else:
                # Orb statement inside asm (FOR, IF, etc)
                self.exec_statement(node, env)
            
            i += 1
    
    def exec_asm_instruction(self, node: AsmInstruction, env: Environment) -> Optional[str]:
        """
        Execute a single orb42_core_v2 ASM instruction.
        
        ISA reference:
          Base:  LOADW, STOREW, LOADDP, STOREDP, MOV, ALU, ALUI, LUI,
                 BEQ, BNE, BLT, BGE, JAL, LEA, ADDQ, NOP, HLT
          Copro: SYS, SINCOS, ATAN2, PSEL, PMAC, PCLR, PLUT, PDRIFT,
                 PWAVE, CDIV, RSQRT, VLOADL, VLOADB, VOP, VREAD, VDOTRD
          
        Registers: D0-D7 (data), A0-A7 (address), DP (data page)
        Variable bridging: identifier operands read/write Orb variables
        """
        op = node.op.upper()
        operands = node.operands
        
        def resolve(operand: AsmOperand) -> Any:
            if operand.kind == "register":
                return self.registers.get(operand.value)
            elif operand.kind == "immediate":
                raw = operand.value
                if raw.startswith("#"):
                    raw = raw[1:]
                try:
                    if raw.startswith("0x") or raw.startswith("0X"):
                        return int(raw, 16)
                    elif "." in raw:
                        return float(raw)
                    else:
                        return int(raw)
                except ValueError:
                    return 0
            elif operand.kind == "expr_immediate":
                return self.eval_expr(operand.expression, env)
            elif operand.kind == "variable":
                return env.get(operand.value)
            elif operand.kind == "indirect":
                return self.registers.get(operand.value)
            elif operand.kind == "indexed":
                base = self.registers.get(operand.value)
                offset = self.eval_expr(operand.offset, env)
                return int(base) + int(offset)
            elif operand.kind == "label_ref":
                return operand.value
            return 0
        
        def store(operand: AsmOperand, value: Any):
            if operand.kind == "register":
                self.registers.set(operand.value, value)
            elif operand.kind == "variable":
                env.set(operand.value, value)
            elif operand.kind in ("indirect", "indexed"):
                self.registers.set(operand.value, value)
        
        def is_reg(operand: AsmOperand) -> bool:
            return operand.kind == "register"
        
        def reg_name(operand: AsmOperand) -> str:
            return operand.value.upper() if operand else ""
        
        nops = len(operands)
        
        # ============================================================
        #  BASE ISA
        # ============================================================
        
        # ---- MOV Dd, Ds (register-to-register, D↔A crossing) ----
        if op == "MOV":
            if nops >= 2:
                val = resolve(operands[1])
                store(operands[0], val)
        
        # ---- ALU Dd, Ds, Dt, <aluop> ----
        # 3-register ALU: result = Ds <op> Dt, stored in Dd
        # The aluop is the 4th operand as a keyword identifier
        elif op == "ALU":
            if nops >= 4:
                ds = resolve(operands[1])
                dt = resolve(operands[2])
                aluop = operands[3].value.upper() if operands[3].kind in ("variable", "label_ref") else str(operands[3].value).upper()
                result = self._alu_op(aluop, ds, dt)
                store(operands[0], result)
                self.registers.update_flags(result)
            elif nops >= 3:
                # ALU Dd, Ds, Dt — default ADD
                ds = resolve(operands[1])
                dt = resolve(operands[2])
                result = self._alu_op("ADD", ds, dt)
                store(operands[0], result)
                self.registers.update_flags(result)
        
        # ---- ALUI Dd, Ds, imm ----
        # Register-immediate ALU: Dd = Ds + sign_ext(imm)
        elif op == "ALUI":
            if nops >= 3:
                ds = resolve(operands[1])
                imm = resolve(operands[2])
                result = int(ds) + int(imm)
                store(operands[0], result)
                self.registers.update_flags(result)
        
        # ---- LUI Dd, imm ---- 
        # Load upper immediate: Dd = imm << 16
        elif op == "LUI":
            if nops >= 2:
                imm = resolve(operands[1])
                result = int(imm) << 16
                store(operands[0], result)
        
        # ---- LOADW Dd, [An, offset] ----
        # In interpreter: reads from Orb env or conceptual memory
        elif op == "LOADW":
            if nops >= 2:
                addr = resolve(operands[1])
                # In the interpreter, memory access is conceptual
                # For variable bridging, if operand is a variable, read it
                store(operands[0], addr)
        
        # ---- STOREW Dd, [An, offset] ----
        elif op == "STOREW":
            if nops >= 2:
                val = resolve(operands[0])
                store(operands[1], val)
        
        # ---- LOADDP Dd, #offset ----
        # Dd = mem[DP + offset << 2]
        elif op == "LOADDP":
            if nops >= 2:
                offset = resolve(operands[1])
                addr = self.registers.dp + (int(offset) << 2)
                store(operands[0], addr)
        
        # ---- STOREDP Dd, #offset ----
        # mem[DP + offset << 2] = Dd
        elif op == "STOREDP":
            if nops >= 2:
                pass  # Conceptual store to peripheral — no actual memory in interpreter
        
        # ---- LEA Dd, [An, offset] ----
        # Load effective address: Dd = An + sign_ext(offset)
        elif op == "LEA":
            if nops >= 2:
                addr = resolve(operands[1])
                store(operands[0], addr)
        
        # ---- ADDQ An, imm ----
        # Quick add 4-bit signed to address register
        elif op == "ADDQ":
            if nops >= 2:
                current = resolve(operands[0])
                imm = resolve(operands[1])
                store(operands[0], int(current) + int(imm))
        
        # ============================================================
        #  BRANCHES (compare two registers, not flags)
        # ============================================================
        
        # ---- BEQ Ds, Dt, target ----
        elif op == "BEQ":
            if nops >= 3:
                ds = resolve(operands[0])
                dt = resolve(operands[1])
                if ds == dt:
                    target = resolve(operands[2])
                    return f"__asm_jmp:{target}"
            elif nops >= 2:
                # BEQ Ds, target (compare to zero)
                ds = resolve(operands[0])
                if ds == 0:
                    target = resolve(operands[1])
                    return f"__asm_jmp:{target}"
        
        elif op == "BNE":
            if nops >= 3:
                ds = resolve(operands[0])
                dt = resolve(operands[1])
                if ds != dt:
                    target = resolve(operands[2])
                    return f"__asm_jmp:{target}"
            elif nops >= 2:
                ds = resolve(operands[0])
                if ds != 0:
                    target = resolve(operands[1])
                    return f"__asm_jmp:{target}"
        
        elif op == "BLT":
            if nops >= 3:
                ds = resolve(operands[0])
                dt = resolve(operands[1])
                if isinstance(ds, (int, float)) and isinstance(dt, (int, float)):
                    if ds < dt:
                        target = resolve(operands[2])
                        return f"__asm_jmp:{target}"
        
        elif op == "BGE":
            if nops >= 3:
                ds = resolve(operands[0])
                dt = resolve(operands[1])
                if isinstance(ds, (int, float)) and isinstance(dt, (int, float)):
                    if ds >= dt:
                        target = resolve(operands[2])
                        return f"__asm_jmp:{target}"
        
        # ---- JAL Dd, [An, offset] ----
        # Jump and link: Dd = PC+4, PC = An + offset
        elif op == "JAL":
            if nops >= 2:
                target = resolve(operands[1])
                # In interpreter, we don't track PC — just jump
                return f"__asm_jmp:{target}"
        
        # ============================================================
        #  SYSTEM / COPROCESSOR
        # ============================================================
        
        # ---- SYS (NOP / HALT / SETDP) ----
        elif op == "SYS":
            if nops >= 1:
                sub = operands[0].value.upper() if operands[0].kind == "variable" else str(operands[0].value).upper()
                if sub == "HALT":
                    raise OrbHalt("SYS HALT")
                elif sub == "NOP":
                    pass
                elif sub == "SETDP":
                    if nops >= 2:
                        val = resolve(operands[1])
                        self.registers.dp = int(val)
        
        # ---- SETDP Ds (shorthand for SYS SETDP) ----
        elif op == "SETDP":
            if nops >= 1:
                val = resolve(operands[0])
                self.registers.dp = int(val)
        
        # ---- SINCOS ----
        # D0 = angle in, D0 = cos out, D1 = sin out
        elif op == "SINCOS":
            angle = self.registers.get("D0")
            if isinstance(angle, (int, float)):
                # Interpret as fixed-point angle or radians
                rad = float(angle) / 1000.0  # Simple scaling
                self.registers.set("D0", math.cos(rad))
                self.registers.set("D1", math.sin(rad))
        
        # ---- ATAN2 ----
        # D0 = x in, D1 = y in. D0 = magnitude out, D2 = angle out
        elif op == "ATAN2":
            x = self.registers.get("D0")
            y = self.registers.get("D1")
            if isinstance(x, (int, float)) and isinstance(y, (int, float)):
                mag = math.sqrt(float(x)**2 + float(y)**2)
                ang = math.atan2(float(y), float(x))
                self.registers.set("D0", mag)
                self.registers.set("D2", ang)
        
        # ---- PSEL Ds — phaser core select ----
        elif op == "PSEL":
            if nops >= 1:
                core = resolve(operands[0])
                self._phaser_selected = int(core)
        
        # ---- PMAC Dd, addr — fire phaser MAC, read result ----
        elif op == "PMAC":
            if nops >= 1:
                val = resolve(operands[0])
                # Simulate MAC: accumulate
                if not hasattr(self, '_phaser_mac_acc'):
                    self._phaser_mac_acc = 0
                self._phaser_mac_acc += int(val)
                self.registers.set("D0", self._phaser_mac_acc)
        
        # ---- PCLR — clear phaser MAC accumulator ----
        elif op == "PCLR":
            self._phaser_mac_acc = 0
        
        # ---- PLUT Ds — write phaser LUT data ----
        elif op == "PLUT":
            pass  # Conceptual — LUT write
        
        # ---- PDRIFT Dd — read composite phaser drift XOR ----
        elif op == "PDRIFT":
            if nops >= 1:
                # Simulate drift signature (deterministic from phaser state)
                drift = 0x0000
                for i in range(7):
                    drift ^= (i * 29 + 137) & 0xFFFF
                store(operands[0], drift)
        
        # ---- PWAVE Dd — read phaser waveform output ----
        elif op == "PWAVE":
            if nops >= 1:
                # Simulate waveform sample
                store(operands[0], 0)
        
        # ---- CDIV Dd, Ds — D0 = Dd/Ds, D1 = Dd%Ds ----
        elif op == "CDIV":
            if nops >= 2:
                dividend = resolve(operands[0])
                divisor = resolve(operands[1])
                if isinstance(dividend, (int, float)) and isinstance(divisor, (int, float)):
                    if int(divisor) != 0:
                        self.registers.set("D0", int(dividend) // int(divisor))
                        self.registers.set("D1", int(dividend) % int(divisor))
                    else:
                        self.registers.set("D0", 0)
                        self.registers.set("D1", 0)
        
        # ---- RSQRT Dd — D0 = 1/sqrt(Dd) ----
        elif op == "RSQRT":
            if nops >= 1:
                val = resolve(operands[0])
                if isinstance(val, (int, float)) and float(val) > 0:
                    self.registers.set("D0", 1.0 / math.sqrt(float(val)))
                else:
                    self.registers.set("D0", 0)
        
        # ---- VLOADL Ds, lane — Vec A[lane] = Ds ----
        elif op == "VLOADL":
            pass  # Vector unit conceptual
        
        # ---- VLOADB Ds, lane — Vec B[lane] = Ds ----
        elif op == "VLOADB":
            pass  # Vector unit conceptual
        
        # ---- VOP op — execute vector operation ----
        elif op == "VOP":
            pass  # Vector unit conceptual
        
        # ---- VREAD Dd, lane — Dd = Vec R[lane] ----
        elif op == "VREAD":
            if nops >= 1:
                store(operands[0], 0)  # Conceptual
        
        # ---- VDOTRD Dd — Dd = dot product accumulator ----
        elif op == "VDOTRD":
            if nops >= 1:
                store(operands[0], 0)  # Conceptual
        
        # ============================================================
        #  ALU SUB-OPERATIONS (standalone, outside ALU instruction)
        # ============================================================
        # These handle cases where ALU ops appear as standalone
        # mnemonics: ADD D0, D1, D2 (shorthand for ALU D0, D1, D2, ADD)
        
        elif op in ("ADD", "SUB", "MUL", "AND", "OR", "XOR", "SHL", "SHR",
                     "ASR", "MIN", "MAX", "SATADD", "SATSUB", "ROR"):
            if nops == 3:
                ds = resolve(operands[1])
                dt = resolve(operands[2])
                result = self._alu_op(op, ds, dt)
                store(operands[0], result)
                self.registers.update_flags(result)
            elif nops == 2:
                ds = resolve(operands[0])
                dt = resolve(operands[1])
                result = self._alu_op(op, ds, dt)
                store(operands[0], result)
                self.registers.update_flags(result)
        
        elif op == "CMP":
            if nops >= 2:
                ds = resolve(operands[0])
                dt = resolve(operands[1])
                diff = int(ds) - int(dt) if isinstance(ds, (int, float)) and isinstance(dt, (int, float)) else 0
                self.registers.update_flags(diff)
        
        elif op in ("CLZ", "ABS", "NOT", "BREV", "BSWAP", "POPCNT", "SEXT8", "SEXT16"):
            if nops >= 1:
                val = resolve(operands[0])
                result = self._alu_unary(op, val)
                store(operands[0], result)
                self.registers.update_flags(result)
        
        elif op == "MULH":
            if nops >= 2:
                a = resolve(operands[0])
                b = resolve(operands[1])
                result = (int(a) * int(b)) >> 32
                store(operands[0], result)
        
        # ---- Stack (not in core ISA but available) ----
        elif op == "PUSH":
            if nops >= 1:
                val = resolve(operands[0])
                if not hasattr(self.registers, 'stack'):
                    self.registers.stack = []
                self.registers.stack.append(val)
        
        elif op == "POP":
            if nops >= 1 and hasattr(self.registers, 'stack') and self.registers.stack:
                store(operands[0], self.registers.stack.pop())
        
        # ---- NOP / HLT ----
        elif op == "NOP":
            pass
        
        elif op == "HLT":
            raise OrbHalt("HLT instruction")
        
        return None
    
    def _alu_op(self, op: str, a: Any, b: Any) -> Any:
        """Execute a 24-operation ALU operation matching orb42_core_v2."""
        ia = int(a) if isinstance(a, (int, float)) else 0
        ib = int(b) if isinstance(b, (int, float)) else 0
        
        ops = {
            "ADD":    lambda: ia + ib,
            "SUB":    lambda: ia - ib,
            "AND":    lambda: ia & ib,
            "OR":     lambda: ia | ib,
            "XOR":    lambda: ia ^ ib,
            "SHL":    lambda: ia << (ib & 0x1F),
            "SHR":    lambda: (ia & 0xFFFFFFFF) >> (ib & 0x1F),
            "ASR":    lambda: ia >> (ib & 0x1F),  # Python handles sign
            "CMP":    lambda: ia - ib,
            "MUL":    lambda: ia * ib,
            "MULH":   lambda: (ia * ib) >> 32,
            "MIN":    lambda: min(ia, ib),
            "MAX":    lambda: max(ia, ib),
            "SATADD": lambda: max(-2**31, min(2**31 - 1, ia + ib)),
            "SATSUB": lambda: max(-2**31, min(2**31 - 1, ia - ib)),
            "ROR":    lambda: ((ia & 0xFFFFFFFF) >> (ib & 0x1F)) | ((ia << (32 - (ib & 0x1F))) & 0xFFFFFFFF),
        }
        
        fn = ops.get(op)
        if fn:
            return fn()
        return ia + ib  # Default ADD
    
    def _alu_unary(self, op: str, val: Any) -> Any:
        """Execute unary ALU operations."""
        iv = int(val) if isinstance(val, (int, float)) else 0
        
        if op == "CLZ":
            if iv == 0:
                return 32
            count = 0
            for bit in range(31, -1, -1):
                if iv & (1 << bit):
                    break
                count += 1
            return count
        elif op == "ABS":
            return abs(iv)
        elif op == "NOT":
            return ~iv & 0xFFFFFFFF
        elif op == "BREV":
            result = 0
            for i in range(32):
                if iv & (1 << i):
                    result |= 1 << (31 - i)
            return result
        elif op == "BSWAP":
            return (((iv >> 24) & 0xFF) |
                    ((iv >> 8) & 0xFF00) |
                    ((iv << 8) & 0xFF0000) |
                    ((iv << 24) & 0xFF000000))
        elif op == "POPCNT":
            return bin(iv & 0xFFFFFFFF).count('1')
        elif op == "SEXT8":
            v = iv & 0xFF
            return v - 256 if v > 127 else v
        elif op == "SEXT16":
            v = iv & 0xFFFF
            return v - 65536 if v > 32767 else v
        
        return iv
    
    # ============================================================
    #  EXPRESSION EVALUATION
    # ============================================================
    
    def eval_expr(self, node: Any, env: Environment) -> Any:
        """Evaluate an expression node, return a Python value."""
        if node is None:
            return 0
        
        if isinstance(node, IntLiteral):
            return node.value
        
        elif isinstance(node, FloatLiteral):
            return node.value
        
        elif isinstance(node, HexLiteral):
            return node.value
        
        elif isinstance(node, StringLiteral):
            return node.value
        
        elif isinstance(node, Identifier):
            # Check asm labels first
            if node.name in self.asm_labels:
                return self.asm_labels[node.name]
            return env.get(node.name)
        
        elif isinstance(node, VecLiteral):
            elements = [float(self.eval_expr(e, env)) for e in node.elements]
            return OrbVec(data=elements, width=len(elements))
        
        elif isinstance(node, BinaryOp):
            left = self.eval_expr(node.left, env)
            right = self.eval_expr(node.right, env)
            return self._binary_op(node.op, left, right)
        
        elif isinstance(node, UnaryOp):
            operand = self.eval_expr(node.operand, env)
            if node.op == "-":
                return -operand if isinstance(operand, (int, float)) else operand
            elif node.op == "~":
                return ~int(operand) if isinstance(operand, (int, float)) else operand
            elif node.op == "NOT":
                return not self._truthy(operand)
            return operand
        
        elif isinstance(node, IndexExpr):
            target = self.eval_expr(node.target, env)
            index = self.eval_expr(node.index, env)
            if isinstance(target, OrbVec):
                return target.data[int(index)]
            elif isinstance(target, list):
                return target[int(index)]
            elif isinstance(target, str):
                return target[int(index)]
            raise OrbRuntimeError(f"Cannot index {type(target).__name__}", node)
        
        elif isinstance(node, DotExpr):
            target = self.eval_expr(node.target, env)
            if isinstance(target, dict):
                return target.get(node.field_name, 0)
            raise OrbRuntimeError(f"Cannot access .{node.field_name}", node)
        
        elif isinstance(node, CallExpr):
            return self._builtin_call(node.func_name, node.args, env)
        
        # Fallback: might be a raw value passed in
        if isinstance(node, (int, float, str)):
            return node
        
        raise OrbRuntimeError(f"Cannot evaluate: {type(node).__name__}")
    
    def _binary_op(self, op: str, left: Any, right: Any) -> Any:
        """Evaluate a binary operation."""
        # String concatenation
        if isinstance(left, str) or isinstance(right, str):
            if op == "+":
                return str(left) + str(right)
            elif op in ("==", "!=", "<", ">", "<=", ">="):
                sl, sr = str(left), str(right)
                ops = {"==": sl == sr, "!=": sl != sr,
                       "<": sl < sr, ">": sl > sr,
                       "<=": sl <= sr, ">=": sl >= sr}
                return 1 if ops.get(op, False) else 0
        
        # Numeric operations
        try:
            l = float(left) if not isinstance(left, (int, float)) else left
            r = float(right) if not isinstance(right, (int, float)) else right
        except (TypeError, ValueError):
            return 0
        
        ops = {
            "+": lambda: l + r,
            "-": lambda: l - r,
            "*": lambda: l * r,
            "/": lambda: l / r if r != 0 else 0,
            "%": lambda: l % r if r != 0 else 0,
            "==": lambda: 1 if l == r else 0,
            "!=": lambda: 1 if l != r else 0,
            "<": lambda: 1 if l < r else 0,
            ">": lambda: 1 if l > r else 0,
            "<=": lambda: 1 if l <= r else 0,
            ">=": lambda: 1 if l >= r else 0,
            "AND": lambda: 1 if l and r else 0,
            "OR": lambda: 1 if l or r else 0,
        }
        
        fn = ops.get(op)
        if fn:
            result = fn()
            # Preserve int type when possible
            if isinstance(left, int) and isinstance(right, int) and op not in ("/",):
                try:
                    return int(result)
                except (TypeError, ValueError):
                    pass
            return result
        
        return 0
    
    def _truthy(self, value: Any) -> bool:
        """Determine if a value is truthy."""
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return len(value) > 0
        if isinstance(value, OrbVec):
            return any(v != 0 for v in value.data)
        return bool(value)
    
    def _builtin_call(self, name: str, args: List, env: Environment) -> Any:
        """Handle built-in function calls."""
        evaluated = [self.eval_expr(a, env) for a in args]
        
        builtins = {
            "abs": lambda a: abs(a[0]),
            "int": lambda a: int(a[0]),
            "float": lambda a: float(a[0]),
            "str": lambda a: str(a[0]),
            "len": lambda a: len(a[0]) if hasattr(a[0], '__len__') else (a[0].width if isinstance(a[0], OrbVec) else 0),
            "sqrt": lambda a: math.sqrt(float(a[0])),
            "sin": lambda a: math.sin(float(a[0])),
            "cos": lambda a: math.cos(float(a[0])),
            "floor": lambda a: int(math.floor(float(a[0]))),
            "ceil": lambda a: int(math.ceil(float(a[0]))),
            "min": lambda a: min(a),
            "max": lambda a: max(a),
        }
        
        fn = builtins.get(name.lower())
        if fn:
            return fn(evaluated)
        
        raise OrbRuntimeError(f"Unknown function: {name}")
    
    # ============================================================
    #  STATE INSPECTION (for geometric view)
    # ============================================================
    
    def get_state(self) -> Dict:
        """
        Get the complete interpreter state.
        Used by the geometric view to render live values.
        """
        return {
            "halted": self.halted,
            "halt_message": self.halt_message,
            "instances": {
                name: {
                    "module_type": inst.module_type,
                    "comm_mode": inst.comm_mode,
                    "executed": inst.executed,
                    "halted": inst.halted,
                    "in_ports": {
                        p.name: {"value": p.value, "type": p.type_name}
                        for p in inst.in_ports.values()
                    },
                    "out_ports": {
                        p.name: {"value": p.value, "type": p.type_name}
                        for p in inst.out_ports.values()
                    },
                    "variables": inst.env.dump(),
                    "messages": {
                        ch: {"pending": len(q.queue)}
                        for ch, q in inst.message_channels.items()
                    },
                }
                for name, inst in self.instances.items()
            },
            "shared_state": {
                name: {"value": s.value, "locked_by": s.locked_by}
                for name, s in self.shared_states.items()
            },
            "registers": self.registers.dump(),
            "globals": self.global_env.dump(),
            "trace_length": len(self.trace),
            "inspect_log": self.inspect_log,
        }


# ============================================================
#  SELF-TEST
# ============================================================

if __name__ == "__main__":
    test_source = r"""
// ============================================
// Orb Interpreter Test Program
// ============================================

MODULE amplifier DATAFLOW
PORTS
    IN  signal AS VEC[4]
    IN  gain   AS FLOAT
    OUT output AS VEC[4]
END PORTS
{
    DIM temp AS VEC[4]
    DIM i AS INT = 0
    
    VLOAD temp FROM signal
    
    FOR i = 0 TO 3
        temp[i] = temp[i] * gain
    NEXT i
    
    VSTORE temp TO output
    
    IF gain > 1.0 THEN
        PRINT "Amplifying by ", gain
    ELIF gain == 1.0 THEN
        PRINT "Unity gain"
    ELSE
        PRINT "Attenuating"
    END IF
    
    @done:
    INSPECT temp
    
    // BBC BASIC inline asm — orb42_core_v2 variable bridging
    [
        ALU D0, D0, D0, XOR     // D0 = 0
        MOV D1, gain             // Variable bridge: read Orb var
        ALUI D2, D0, 10          // D2 = 10
        ALU D1, D1, D2, ADD      // D1 = gain + 10
        MOV gain, D1             // Write back to Orb var
    ]
    
    PRINT "Gain after asm: ", gain
}
END MODULE

MODULE mixer DATAFLOW
PORTS
    IN  input_a AS VEC[4]
    IN  input_b AS VEC[4]
    OUT output  AS VEC[4]
END PORTS
{
    DIM result AS VEC[4]
    DIM i AS INT = 0
    
    VLOAD result FROM input_a
    
    FOR i = 0 TO 3
        result[i] = result[i] + input_b[i]
    NEXT i
    
    VSTORE result TO output
    PRINT "Mixed output: ", output
}
END MODULE

MODULE logger MESSAGE
{
    DIM msg AS STRING = "waiting"
    PRINT "Logger active: ", msg
}
END MODULE

BOARD main_board
    PLACE amplifier AS amp1
    PLACE amplifier AS amp2
    PLACE mixer     AS mix
    PLACE logger    AS log
    
    WIRE amp1.output TO mix.input_a
    WIRE amp2.output TO mix.input_b
    
    SET amp1.gain   = 2.0
    SET amp1.signal = <1.0, 2.0, 3.0, 4.0>
    SET amp2.gain   = 0.5
    SET amp2.signal = <10.0, 20.0, 30.0, 40.0>
    
    SHARE gain_state BETWEEN amp1, amp2, mix
END BOARD
"""
    
    print("=" * 60)
    print("  ORB LANGUAGE INTERPRETER — TEST RUN")
    print("=" * 60)
    print()
    
    output_log = []
    def capture_output(*args):
        line = " ".join(str(a) for a in args)
        output_log.append(line)
        print(f"  >> {line}")
    
    interp = Interpreter(output_fn=capture_output)
    
    try:
        interp.run_source(test_source, "test.orb")
        
        print()
        print("-" * 40)
        print("  Execution complete.")
        print(f"  Output lines: {len(output_log)}")
        print(f"  Trace steps:  {len(interp.trace)}")
        print(f"  Halted:       {interp.halted}")
        
        # Show state
        state = interp.get_state()
        print()
        print("  Instance states:")
        for name, inst_state in state["instances"].items():
            print(f"    {name} ({inst_state['module_type']}):")
            if inst_state["in_ports"]:
                for pname, pval in inst_state["in_ports"].items():
                    print(f"      IN  {pname} = {pval['value']}")
            if inst_state["out_ports"]:
                for pname, pval in inst_state["out_ports"].items():
                    print(f"      OUT {pname} = {pval['value']}")
            # Show a few key variables
            vars_ = inst_state["variables"]
            for vname, vval in list(vars_.items())[:5]:
                if vname not in [p for p in inst_state["in_ports"]] + \
                                [p for p in inst_state["out_ports"]]:
                    print(f"      VAR {vname} = {vval}")
        
        if state["registers"]:
            non_flag = {k: v for k, v in state["registers"].items() if k != "FLAGS"}
            if non_flag:
                print()
                print("  Registers with values:")
                for rname, rval in non_flag.items():
                    print(f"    {rname} = {rval}")
        
        if state["inspect_log"]:
            print()
            print("  INSPECT log:")
            for entry in state["inspect_log"]:
                print(f"    {entry['target']} = {entry['value']} (L{entry['line']})")
        
    except OrbRuntimeError as e:
        print(f"  RUNTIME ERROR: {e}")
    except OrbHalt as h:
        print(f"  HALTED: {h.message}")
        state = interp.get_state()
        print(f"  State preserved ({len(state['instances'])} instances)")
