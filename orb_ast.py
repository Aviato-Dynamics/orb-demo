"""
ORB LANGUAGE — AST NODE DEFINITIONS
=====================================
Every node carries source location (line, col) so the
geometric renderer can map visuals back to source.

Nodes are grouped by level:
  - Program level (modules, boards, imports)
  - Module internals (ports, shared blocks, statements)
  - Board internals (place, wire, route, share, set, probe, export)
  - Statements (control flow, assignment, I/O, debug)
  - Expressions (arithmetic, logic, comparison, indexing)
  - ASM (instructions, labels, directives)
  - Vector operations (verb-level SIMD)
  - Communication (send, recv, lock)
"""

from dataclasses import dataclass, field
from typing import List, Optional, Union, Any
from orb_lexer import TokenType


# ============================================================
#  BASE NODE
# ============================================================

@dataclass
class ASTNode:
    """Base class for all AST nodes. Carries source location."""
    line: int = 0
    col: int = 0


# ============================================================
#  PROGRAM LEVEL
# ============================================================

@dataclass
class Program(ASTNode):
    """Top-level program: a sequence of modules, boards, imports, and statements."""
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class ModuleDecl(ASTNode):
    """MODULE name MODE ... END MODULE"""
    name: str = ""
    comm_mode: str = ""              # "DATAFLOW" | "MESSAGE" | "SHARED"
    ports: List['PortDecl'] = field(default_factory=list)
    shared_vars: List['VarDecl'] = field(default_factory=list)
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class BoardDecl(ASTNode):
    """BOARD name ... END BOARD"""
    name: str = ""
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class ImportStmt(ASTNode):
    """IMPORT "path" """
    path: str = ""


# ============================================================
#  MODULE INTERFACE
# ============================================================

@dataclass
class PortDecl(ASTNode):
    """IN/OUT/INOUT name AS type [width]"""
    direction: str = ""              # "IN" | "OUT" | "INOUT"
    name: str = ""
    type_name: str = ""
    vec_width: Optional[int] = None  # For VEC[N]


@dataclass
class VarDecl(ASTNode):
    """DIM name AS type [= expr]"""
    name: str = ""
    type_name: str = ""
    vec_width: Optional[int] = None
    initialiser: Optional['Expression'] = None


@dataclass
class ConstDecl(ASTNode):
    """CONST name AS type = expr"""
    name: str = ""
    type_name: str = ""
    value: Optional['Expression'] = None


# ============================================================
#  BOARD INTERNALS
# ============================================================

@dataclass
class PlaceStmt(ASTNode):
    """PLACE module_type AS instance_name [AT x, y]"""
    module_type: str = ""
    instance_name: str = ""
    pos_x: Optional['Expression'] = None
    pos_y: Optional['Expression'] = None


@dataclass
class WireStmt(ASTNode):
    """WIRE a.port TO b.port"""
    src_module: str = ""
    src_port: str = ""
    dst_module: str = ""
    dst_port: str = ""


@dataclass
class RouteStmt(ASTNode):
    """ROUTE a.channel TO b.channel"""
    src_module: str = ""
    src_port: str = ""
    dst_module: str = ""
    dst_port: str = ""


@dataclass
class ShareStmt(ASTNode):
    """SHARE state_name BETWEEN mod1, mod2, ..."""
    state_name: str = ""
    modules: List[str] = field(default_factory=list)


@dataclass
class SetStmt(ASTNode):
    """SET module.port = expression"""
    module: str = ""
    port: str = ""
    value: Optional['Expression'] = None


@dataclass
class ProbeStmt(ASTNode):
    """PROBE module.port [AS "label"]"""
    module: str = ""
    port: str = ""
    label: Optional[str] = None


@dataclass
class ExportStmt(ASTNode):
    """EXPORT module.port AS external_name"""
    module: str = ""
    port: str = ""
    external_name: str = ""


# ============================================================
#  STATEMENTS
# ============================================================

@dataclass
class Assignment(ASTNode):
    """target = expression  or  target[index] = expression"""
    target: str = ""
    index: Optional['Expression'] = None   # None = simple, set = indexed
    value: Optional['Expression'] = None


@dataclass
class IfStmt(ASTNode):
    """IF cond THEN ... [ELIF ...] [ELSE ...] END IF"""
    condition: Optional['Expression'] = None
    then_body: List[ASTNode] = field(default_factory=list)
    elif_clauses: List['ElifClause'] = field(default_factory=list)
    else_body: List[ASTNode] = field(default_factory=list)


@dataclass
class ElifClause(ASTNode):
    """ELIF cond THEN ..."""
    condition: Optional['Expression'] = None
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class ForStmt(ASTNode):
    """FOR var = start TO end [STEP step] ... NEXT [var]"""
    var_name: str = ""
    start: Optional['Expression'] = None
    end: Optional['Expression'] = None
    step: Optional['Expression'] = None
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class WhileStmt(ASTNode):
    """WHILE cond ... WEND"""
    condition: Optional['Expression'] = None
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class GotoStmt(ASTNode):
    """GOTO label"""
    target: str = ""


@dataclass
class GosubStmt(ASTNode):
    """GOSUB label"""
    target: str = ""


@dataclass
class ReturnStmt(ASTNode):
    """RETURN [expression]"""
    value: Optional['Expression'] = None


@dataclass
class LabelStmt(ASTNode):
    """@label:"""
    name: str = ""


@dataclass
class PrintStmt(ASTNode):
    """PRINT expr, expr, ..."""
    values: List['Expression'] = field(default_factory=list)


@dataclass
class InputStmt(ASTNode):
    """INPUT [prompt,] variable"""
    prompt: Optional[str] = None
    variable: str = ""


@dataclass
class HaltStmt(ASTNode):
    """HALT ["message"]"""
    message: Optional[str] = None


@dataclass
class InspectStmt(ASTNode):
    """INSPECT variable"""
    target: str = ""


@dataclass
class Block(ASTNode):
    """{ ... } block"""
    body: List[ASTNode] = field(default_factory=list)


# ============================================================
#  VECTOR OPERATIONS (VERB LEVEL)
# ============================================================

@dataclass
class VecArith(ASTNode):
    """VADD/VSUB/VMUL/VDIV/VMADD a, b INTO c"""
    op: str = ""                     # "VADD" etc.
    operand_a: Optional['Expression'] = None
    operand_b: Optional['Expression'] = None
    target: str = ""


@dataclass
class VecLoad(ASTNode):
    """VLOAD target FROM source"""
    target: str = ""
    source: Optional['Expression'] = None


@dataclass
class VecStore(ASTNode):
    """VSTORE source TO target"""
    source: str = ""
    target: Optional['Expression'] = None


@dataclass
class VecReduce(ASTNode):
    """VSUM/VDOT/VMIN/VMAX source INTO target"""
    op: str = ""
    source: Optional['Expression'] = None
    target: str = ""


# ============================================================
#  COMMUNICATION
# ============================================================

@dataclass
class SendStmt(ASTNode):
    """SEND expr TO module[.channel]"""
    value: Optional['Expression'] = None
    target_module: str = ""
    target_channel: Optional[str] = None


@dataclass
class RecvStmt(ASTNode):
    """RECV var FROM module[.channel] [TIMEOUT expr]"""
    variable: str = ""
    source_module: str = ""
    source_channel: Optional[str] = None
    timeout: Optional['Expression'] = None


@dataclass
class ReadPortStmt(ASTNode):
    """READPORT port INTO variable"""
    port: str = ""
    variable: str = ""


@dataclass
class LockStmt(ASTNode):
    """LOCK target ... UNLOCK target"""
    target: str = ""
    body: List[ASTNode] = field(default_factory=list)


# ============================================================
#  INLINE ASSEMBLY (BBC BASIC STYLE)
# ============================================================

@dataclass
class AsmBlock(ASTNode):
    """ASM [name] ... END ASM  (named block form)"""
    name: Optional[str] = None
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class AsmInline(ASTNode):
    """[ ... ]  (BBC BASIC inline form)"""
    body: List[ASTNode] = field(default_factory=list)


@dataclass
class AsmInstruction(ASTNode):
    """op operand, operand, ..."""
    op: str = ""
    operands: List['AsmOperand'] = field(default_factory=list)


@dataclass
class AsmOperand(ASTNode):
    """A single operand in an asm instruction."""
    kind: str = ""       # "register", "immediate", "expr_immediate",
                         # "variable", "indirect", "indexed", "label_ref"
    value: Any = None    # Register name, immediate value, identifier, etc.
    offset: Optional['Expression'] = None   # For indexed: [R0, offset]
    expression: Optional['Expression'] = None  # For #(expr)


@dataclass
class AsmLabelDef(ASTNode):
    """.label: — definition"""
    name: str = ""


@dataclass
class AsmLabelRef(ASTNode):
    """.label — reference (branch target)"""
    name: str = ""


@dataclass
class AsmOpt(ASTNode):
    """OPT expression"""
    value: Optional['Expression'] = None


@dataclass
class AsmEquate(ASTNode):
    """.name EQU expression"""
    name: str = ""
    value: Optional['Expression'] = None


# ============================================================
#  EXPRESSIONS
# ============================================================

@dataclass
class Expression(ASTNode):
    """Base for all expressions."""
    pass


@dataclass
class BinaryOp(Expression):
    """left OP right"""
    op: str = ""
    left: Optional[Expression] = None
    right: Optional[Expression] = None


@dataclass
class UnaryOp(Expression):
    """OP expr (prefix: -, ~, NOT)"""
    op: str = ""
    operand: Optional[Expression] = None


@dataclass
class Identifier(Expression):
    """A named reference."""
    name: str = ""


@dataclass
class IntLiteral(Expression):
    """Integer literal."""
    value: int = 0


@dataclass
class FloatLiteral(Expression):
    """Float literal."""
    value: float = 0.0


@dataclass
class HexLiteral(Expression):
    """Hex literal (stored as int)."""
    value: int = 0
    raw: str = ""     # Original text e.g. "0xFF"


@dataclass
class StringLiteral(Expression):
    """String literal."""
    value: str = ""


@dataclass
class VecLiteral(Expression):
    """<expr, expr, ...>"""
    elements: List[Expression] = field(default_factory=list)


@dataclass
class IndexExpr(Expression):
    """target[index]"""
    target: Optional[Expression] = None
    index: Optional[Expression] = None


@dataclass
class DotExpr(Expression):
    """object.field"""
    target: Optional[Expression] = None
    field_name: str = ""


@dataclass
class CallExpr(Expression):
    """func(args)"""
    func_name: str = ""
    args: List[Expression] = field(default_factory=list)


@dataclass
class CommentNode(ASTNode):
    """Preserved comment (for geometric view annotations)."""
    text: str = ""
