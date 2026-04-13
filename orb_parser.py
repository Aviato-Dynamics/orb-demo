"""
ORB LANGUAGE — PARSER
======================
Recursive descent parser. One method per grammar production.
Converts the token stream from the lexer into an AST.

Every AST node gets source location (line, col) from its
opening token, so the geometric renderer can map back to source.

Key design decisions:
  - After DOT, the next token is always treated as an identifier
    regardless of keyword status (fixes the input/output collision)
  - Vec literal <a, b, c> is parsed by context: if '<' appears
    where an expression is expected and contains commas, it's a vec.
  - Inside inline [...] asm blocks, Orb statements (IF/FOR/WHILE)
    are valid — BBC BASIC style mixing.
  - Comments are preserved as AST nodes for the geometric view.
"""

from typing import List, Optional, Tuple
from orb_lexer import Lexer, Token, TokenType, LexerError
from orb_ast import *


# ============================================================
#  PARSER ERRORS
# ============================================================

class ParseError(Exception):
    def __init__(self, message: str, token: Token):
        self.token = token
        super().__init__(
            f"Parse error at L{token.line}:{token.col}: {message} "
            f"(got {token.type.name} '{token.value}')"
        )


# ============================================================
#  SETS FOR LOOKAHEAD
# ============================================================

# All tokens that can start a statement
STATEMENT_STARTERS = {
    TokenType.DIM, TokenType.CONST,
    TokenType.IF, TokenType.FOR, TokenType.WHILE,
    TokenType.GOTO, TokenType.GOSUB, TokenType.RETURN,
    TokenType.PRINT, TokenType.INPUT,
    TokenType.HALT, TokenType.INSPECT,
    TokenType.VADD, TokenType.VSUB, TokenType.VMUL,
    TokenType.VDIV, TokenType.VMADD,
    TokenType.VLOAD, TokenType.VSTORE,
    TokenType.VSUM, TokenType.VDOT, TokenType.VMIN, TokenType.VMAX,
    TokenType.SEND, TokenType.RECV, TokenType.READPORT,
    TokenType.LOCK,
    TokenType.WIRE, TokenType.ROUTE,
    TokenType.ASM,
    TokenType.LBRACKET,    # Inline asm [...]
    TokenType.LBRACE,      # Block { }
    TokenType.IDENTIFIER,  # Assignment or expression
    TokenType.LABEL,       # @label:
    TokenType.COMMENT,
}

# ASM opcodes — used to detect asm instructions vs Orb statements inside [...]
ASM_OPS = {
    # Base ISA
    TokenType.LOADW, TokenType.STOREW, TokenType.LOADDP, TokenType.STOREDP,
    TokenType.MOV, TokenType.ALU_OP, TokenType.ALUI, TokenType.LUI,
    TokenType.BEQ, TokenType.BNE, TokenType.BLT, TokenType.BGE,
    TokenType.JAL, TokenType.LEA, TokenType.ADDQ,
    TokenType.NOP, TokenType.HLT,
    # ALU sub-ops (can appear as standalone in ALUI context)
    TokenType.ADD, TokenType.SUB, TokenType.MUL, TokenType.CMP,
    TokenType.AND_ASM, TokenType.OR_ASM, TokenType.XOR,
    TokenType.NOT_ASM, TokenType.SHL, TokenType.SHR, TokenType.ASR,
    TokenType.MULH, TokenType.MIN_OP, TokenType.MAX_OP,
    TokenType.CLZ, TokenType.ABS_OP,
    TokenType.SEXT8, TokenType.SEXT16, TokenType.BREV, TokenType.BSWAP,
    TokenType.POPCNT, TokenType.SATADD, TokenType.SATSUB, TokenType.ROR,
    # Coprocessor
    TokenType.SYS, TokenType.SINCOS, TokenType.ATAN2,
    TokenType.PSEL, TokenType.PMAC, TokenType.PCLR, TokenType.PLUT,
    TokenType.PDRIFT, TokenType.PWAVE,
    TokenType.CDIV, TokenType.RSQRT,
    TokenType.VLOADL, TokenType.VLOADB_ASM, TokenType.VOP,
    TokenType.VREAD, TokenType.VDOTRD,
    TokenType.PUSH, TokenType.POP,
}

# Vec verb ops
VEC_ARITH_OPS = {
    TokenType.VADD, TokenType.VSUB, TokenType.VMUL,
    TokenType.VDIV, TokenType.VMADD,
}

VEC_REDUCE_OPS = {
    TokenType.VSUM, TokenType.VDOT, TokenType.VMIN, TokenType.VMAX,
}


# ============================================================
#  PARSER
# ============================================================

class Parser:
    """
    Recursive descent parser for Orb language.
    
    Usage:
        parser = Parser(tokens)
        ast = parser.parse()
    """
    
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos = 0
        self.in_asm = False     # Inside ASM block or inline [asm]
    
    # ---- Token access ----
    
    @property
    def current(self) -> Token:
        if self.pos >= len(self.tokens):
            return Token(TokenType.EOF, '', 0, 0)
        return self.tokens[self.pos]
    
    def peek(self, offset: int = 1) -> Token:
        pos = self.pos + offset
        if pos >= len(self.tokens):
            return Token(TokenType.EOF, '', 0, 0)
        return self.tokens[pos]
    
    def advance(self) -> Token:
        tok = self.current
        self.pos += 1
        return tok
    
    def expect(self, ttype: TokenType) -> Token:
        if self.current.type != ttype:
            raise ParseError(
                f"Expected {ttype.name}", self.current
            )
        return self.advance()
    
    def match(self, *types: TokenType) -> Optional[Token]:
        if self.current.type in types:
            return self.advance()
        return None
    
    def at(self, *types: TokenType) -> bool:
        return self.current.type in types
    
    def skip_newlines(self):
        while self.current.type == TokenType.NEWLINE:
            self.advance()
    
    def expect_newline(self):
        """Expect at least one newline (or EOF)."""
        if self.current.type not in (TokenType.NEWLINE, TokenType.EOF):
            raise ParseError("Expected newline", self.current)
        self.skip_newlines()
    
    def error(self, message: str):
        raise ParseError(message, self.current)
    
    def loc(self, token: Token) -> dict:
        """Extract location from a token for AST node construction."""
        return {"line": token.line, "col": token.col}
    
    # ---- Read an identifier, treating keywords as identifiers after DOT ----
    
    def read_identifier(self) -> str:
        """
        Read an identifier. If the current token is a keyword,
        treat it as an identifier anyway (for dot-access contexts
        and EXPORT/PLACE AS targets).
        """
        tok = self.current
        if tok.type == TokenType.IDENTIFIER:
            self.advance()
            return tok.value
        # Accept keywords as identifiers in these contexts
        if tok.type in KEYWORDS_AS_IDENT:
            self.advance()
            return tok.value
        raise ParseError("Expected identifier", tok)
    
    def read_qualified_name(self) -> Tuple[str, str]:
        """Read module.port — a qualified name."""
        module = self.read_identifier()
        self.expect(TokenType.DOT)
        port = self.read_identifier()
        return module, port
    
    # ============================================================
    #  TOP LEVEL
    # ============================================================
    
    def parse(self) -> Program:
        """Parse the full program."""
        prog = Program(body=[], **self.loc(self.current))
        self.skip_newlines()
        
        while not self.at(TokenType.EOF):
            if self.at(TokenType.MODULE):
                prog.body.append(self.parse_module())
            elif self.at(TokenType.BOARD):
                prog.body.append(self.parse_board())
            elif self.at(TokenType.IMPORT):
                prog.body.append(self.parse_import())
            elif self.at(TokenType.COMMENT):
                prog.body.append(self.parse_comment())
            elif self.at(TokenType.WIRE):
                prog.body.append(self.parse_wire_stmt())
            elif self.at(TokenType.ROUTE):
                prog.body.append(self.parse_route_stmt())
            else:
                prog.body.append(self.parse_statement())
            self.skip_newlines()
        
        return prog
    
    # ============================================================
    #  MODULE
    # ============================================================
    
    def parse_module(self) -> ModuleDecl:
        tok = self.expect(TokenType.MODULE)
        name = self.read_identifier()
        
        # Comm mode
        comm_mode = ""
        if self.at(TokenType.DATAFLOW):
            comm_mode = "DATAFLOW"; self.advance()
        elif self.at(TokenType.MESSAGE):
            comm_mode = "MESSAGE"; self.advance()
        elif self.at(TokenType.KW_SHARED):
            comm_mode = "SHARED"; self.advance()
        
        self.skip_newlines()
        
        # Optional PORTS block
        ports = []
        if self.at(TokenType.PORTS):
            ports = self.parse_ports_block()
            self.skip_newlines()
        
        # Optional SHARED block
        shared_vars = []
        if self.at(TokenType.SHARED_BLOCK):
            shared_vars = self.parse_shared_block()
            self.skip_newlines()
        
        # Body in { ... }
        body = []
        if self.at(TokenType.LBRACE):
            body = self.parse_brace_body()
            self.skip_newlines()
        
        self.expect(TokenType.END_MODULE)
        self.skip_newlines()
        
        return ModuleDecl(
            name=name, comm_mode=comm_mode,
            ports=ports, shared_vars=shared_vars,
            body=body, **self.loc(tok)
        )
    
    def parse_ports_block(self) -> List[PortDecl]:
        self.expect(TokenType.PORTS)
        self.skip_newlines()
        ports = []
        while not self.at(TokenType.END_PORTS):
            if self.at(TokenType.COMMENT):
                self.advance()
                self.skip_newlines()
                continue
            ports.append(self.parse_port_decl())
            self.skip_newlines()
        self.expect(TokenType.END_PORTS)
        return ports
    
    def parse_port_decl(self) -> PortDecl:
        tok = self.current
        direction = ""
        if self.at(TokenType.IN):
            direction = "IN"; self.advance()
        elif self.at(TokenType.OUT):
            direction = "OUT"; self.advance()
        elif self.at(TokenType.INOUT):
            direction = "INOUT"; self.advance()
        else:
            self.error("Expected IN, OUT, or INOUT")
        
        name = self.read_identifier()
        self.expect(TokenType.AS)
        type_name, vec_width = self.parse_type()
        
        return PortDecl(
            direction=direction, name=name,
            type_name=type_name, vec_width=vec_width,
            **self.loc(tok)
        )
    
    def parse_shared_block(self) -> List[VarDecl]:
        self.expect(TokenType.SHARED_BLOCK)
        self.skip_newlines()
        vars_ = []
        while not self.at(TokenType.END_SHARED):
            if self.at(TokenType.COMMENT):
                self.advance()
                self.skip_newlines()
                continue
            vars_.append(self.parse_var_decl())
            self.skip_newlines()
        self.expect(TokenType.END_SHARED)
        return vars_
    
    def parse_type(self) -> Tuple[str, Optional[int]]:
        """Parse a type name, return (type_name, vec_width_or_None)."""
        if self.at(TokenType.INT):
            self.advance(); return ("INT", None)
        elif self.at(TokenType.FLOAT):
            self.advance(); return ("FLOAT", None)
        elif self.at(TokenType.STRING):
            self.advance(); return ("STRING", None)
        elif self.at(TokenType.VEC):
            self.advance()
            width = None
            if self.at(TokenType.LBRACKET):
                self.advance()
                w_tok = self.expect(TokenType.INTEGER_LIT)
                width = int(w_tok.value)
                self.expect(TokenType.RBRACKET)
            return ("VEC", width)
        else:
            self.error("Expected type name (INT, FLOAT, STRING, VEC)")
    
    # ============================================================
    #  BOARD
    # ============================================================
    
    def parse_board(self) -> BoardDecl:
        tok = self.expect(TokenType.BOARD)
        name = self.read_identifier()
        self.skip_newlines()
        
        body = []
        while not self.at(TokenType.END_BOARD):
            if self.at(TokenType.PLACE):
                body.append(self.parse_place_stmt())
            elif self.at(TokenType.WIRE):
                body.append(self.parse_wire_stmt())
            elif self.at(TokenType.ROUTE):
                body.append(self.parse_route_stmt())
            elif self.at(TokenType.SHARE):
                body.append(self.parse_share_stmt())
            elif self.at(TokenType.SET):
                body.append(self.parse_set_stmt())
            elif self.at(TokenType.PROBE):
                body.append(self.parse_probe_stmt())
            elif self.at(TokenType.EXPORT):
                body.append(self.parse_export_stmt())
            elif self.at(TokenType.COMMENT):
                body.append(self.parse_comment())
            else:
                self.error(f"Unexpected token in BOARD: {self.current.type.name}")
            self.skip_newlines()
        
        self.expect(TokenType.END_BOARD)
        self.skip_newlines()
        
        return BoardDecl(name=name, body=body, **self.loc(tok))
    
    def parse_place_stmt(self) -> PlaceStmt:
        tok = self.expect(TokenType.PLACE)
        module_type = self.read_identifier()
        self.expect(TokenType.AS)
        instance_name = self.read_identifier()
        
        pos_x = pos_y = None
        if self.at(TokenType.KW_AT):
            self.advance()
            pos_x = self.parse_expression()
            self.expect(TokenType.COMMA)
            pos_y = self.parse_expression()
        
        return PlaceStmt(
            module_type=module_type, instance_name=instance_name,
            pos_x=pos_x, pos_y=pos_y, **self.loc(tok)
        )
    
    def parse_wire_stmt(self) -> WireStmt:
        tok = self.expect(TokenType.WIRE)
        src_mod, src_port = self.read_qualified_name()
        self.expect(TokenType.TO)
        dst_mod, dst_port = self.read_qualified_name()
        return WireStmt(
            src_module=src_mod, src_port=src_port,
            dst_module=dst_mod, dst_port=dst_port,
            **self.loc(tok)
        )
    
    def parse_route_stmt(self) -> RouteStmt:
        tok = self.expect(TokenType.ROUTE)
        src_mod, src_port = self.read_qualified_name()
        self.expect(TokenType.TO)
        dst_mod, dst_port = self.read_qualified_name()
        return RouteStmt(
            src_module=src_mod, src_port=src_port,
            dst_module=dst_mod, dst_port=dst_port,
            **self.loc(tok)
        )
    
    def parse_share_stmt(self) -> ShareStmt:
        tok = self.expect(TokenType.SHARE)
        state_name = self.read_identifier()
        self.expect(TokenType.BETWEEN)
        modules = [self.read_identifier()]
        while self.at(TokenType.COMMA):
            self.advance()
            modules.append(self.read_identifier())
        return ShareStmt(
            state_name=state_name, modules=modules, **self.loc(tok)
        )
    
    def parse_set_stmt(self) -> SetStmt:
        tok = self.expect(TokenType.SET)
        module, port = self.read_qualified_name()
        self.expect(TokenType.ASSIGN)
        value = self.parse_expression()
        return SetStmt(
            module=module, port=port, value=value, **self.loc(tok)
        )
    
    def parse_probe_stmt(self) -> ProbeStmt:
        tok = self.expect(TokenType.PROBE)
        module, port = self.read_qualified_name()
        label = None
        if self.at(TokenType.AS):
            self.advance()
            label_tok = self.expect(TokenType.STRING_LIT)
            label = label_tok.value[1:-1]  # Strip quotes
        return ProbeStmt(
            module=module, port=port, label=label, **self.loc(tok)
        )
    
    def parse_export_stmt(self) -> ExportStmt:
        tok = self.expect(TokenType.EXPORT)
        module, port = self.read_qualified_name()
        self.expect(TokenType.AS)
        external_name = self.read_identifier()
        return ExportStmt(
            module=module, port=port, external_name=external_name,
            **self.loc(tok)
        )
    
    def parse_import(self) -> ImportStmt:
        tok = self.expect(TokenType.IMPORT)
        path_tok = self.expect(TokenType.STRING_LIT)
        path = path_tok.value[1:-1]  # Strip quotes
        return ImportStmt(path=path, **self.loc(tok))
    
    # ============================================================
    #  STATEMENTS
    # ============================================================
    
    def parse_statement(self) -> ASTNode:
        """Parse a single statement. ASM-aware when inside asm context."""
        tok = self.current
        
        # When inside ASM context, check for asm-specific tokens first
        if self.in_asm:
            if tok.type == TokenType.OPT:
                return self.parse_asm_opt()
            if tok.type in ASM_OPS:
                return self.parse_asm_instruction()
            if tok.type == TokenType.ASM_LABEL and tok.value.endswith(':'):
                return self.parse_asm_label_def()
            if (tok.type == TokenType.ASM_LABEL and
                not tok.value.endswith(':') and
                self.peek().type == TokenType.EQU):
                return self.parse_asm_equate()
        
        if tok.type == TokenType.COMMENT:
            return self.parse_comment()
        elif tok.type == TokenType.DIM:
            return self.parse_var_decl()
        elif tok.type == TokenType.CONST:
            return self.parse_const_decl()
        elif tok.type == TokenType.IF:
            return self.parse_if_stmt()
        elif tok.type == TokenType.FOR:
            return self.parse_for_stmt()
        elif tok.type == TokenType.WHILE:
            return self.parse_while_stmt()
        elif tok.type == TokenType.GOTO:
            return self.parse_goto_stmt()
        elif tok.type == TokenType.GOSUB:
            return self.parse_gosub_stmt()
        elif tok.type == TokenType.RETURN:
            return self.parse_return_stmt()
        elif tok.type == TokenType.LABEL:
            return self.parse_label_stmt()
        elif tok.type == TokenType.PRINT:
            return self.parse_print_stmt()
        elif tok.type == TokenType.INPUT:
            return self.parse_input_stmt()
        elif tok.type == TokenType.HALT:
            return self.parse_halt_stmt()
        elif tok.type == TokenType.INSPECT:
            return self.parse_inspect_stmt()
        elif tok.type in VEC_ARITH_OPS:
            return self.parse_vec_arith()
        elif tok.type == TokenType.VLOAD:
            return self.parse_vec_load()
        elif tok.type == TokenType.VSTORE:
            return self.parse_vec_store()
        elif tok.type in VEC_REDUCE_OPS:
            return self.parse_vec_reduce()
        elif tok.type == TokenType.SEND:
            return self.parse_send_stmt()
        elif tok.type == TokenType.RECV:
            return self.parse_recv_stmt()
        elif tok.type == TokenType.READPORT:
            return self.parse_readport_stmt()
        elif tok.type == TokenType.LOCK:
            return self.parse_lock_stmt()
        elif tok.type == TokenType.WIRE:
            return self.parse_wire_stmt()
        elif tok.type == TokenType.ASM:
            return self.parse_asm_block()
        elif tok.type == TokenType.LBRACKET:
            return self.parse_asm_inline()
        elif tok.type == TokenType.LBRACE:
            return self.parse_block()
        elif tok.type == TokenType.IDENTIFIER:
            return self.parse_assignment_or_expr()
        else:
            self.error(f"Unexpected token at statement level: {tok.type.name}")
    
    def parse_brace_body(self) -> List[ASTNode]:
        """Parse { statements... }"""
        self.expect(TokenType.LBRACE)
        self.skip_newlines()
        body = []
        while not self.at(TokenType.RBRACE):
            body.append(self.parse_statement())
            self.skip_newlines()
        self.expect(TokenType.RBRACE)
        return body
    
    def parse_block(self) -> Block:
        tok = self.current
        body = self.parse_brace_body()
        return Block(body=body, **self.loc(tok))
    
    def parse_comment(self) -> CommentNode:
        tok = self.advance()
        return CommentNode(text=tok.value, **self.loc(tok))
    
    # ---- Declarations ----
    
    def parse_var_decl(self) -> VarDecl:
        tok = self.expect(TokenType.DIM)
        name = self.read_identifier()
        self.expect(TokenType.AS)
        type_name, vec_width = self.parse_type()
        
        init = None
        if self.at(TokenType.ASSIGN):
            self.advance()
            init = self.parse_expression()
        
        return VarDecl(
            name=name, type_name=type_name,
            vec_width=vec_width, initialiser=init,
            **self.loc(tok)
        )
    
    def parse_const_decl(self) -> ConstDecl:
        tok = self.expect(TokenType.CONST)
        name = self.read_identifier()
        self.expect(TokenType.AS)
        type_name, _ = self.parse_type()
        self.expect(TokenType.ASSIGN)
        value = self.parse_expression()
        return ConstDecl(
            name=name, type_name=type_name, value=value,
            **self.loc(tok)
        )
    
    # ---- Control Flow ----
    
    def parse_if_stmt(self) -> IfStmt:
        tok = self.expect(TokenType.IF)
        cond = self.parse_expression()
        self.expect(TokenType.THEN)
        self.skip_newlines()
        
        then_body = []
        while not self.at(TokenType.ELIF, TokenType.ELSE, TokenType.END_IF):
            then_body.append(self.parse_statement())
            self.skip_newlines()
        
        elif_clauses = []
        while self.at(TokenType.ELIF):
            elif_clauses.append(self.parse_elif_clause())
        
        else_body = []
        if self.at(TokenType.ELSE):
            self.advance()
            self.skip_newlines()
            while not self.at(TokenType.END_IF):
                else_body.append(self.parse_statement())
                self.skip_newlines()
        
        self.expect(TokenType.END_IF)
        
        return IfStmt(
            condition=cond, then_body=then_body,
            elif_clauses=elif_clauses, else_body=else_body,
            **self.loc(tok)
        )
    
    def parse_elif_clause(self) -> ElifClause:
        tok = self.expect(TokenType.ELIF)
        cond = self.parse_expression()
        self.expect(TokenType.THEN)
        self.skip_newlines()
        
        body = []
        while not self.at(TokenType.ELIF, TokenType.ELSE, TokenType.END_IF):
            body.append(self.parse_statement())
            self.skip_newlines()
        
        return ElifClause(condition=cond, body=body, **self.loc(tok))
    
    def parse_for_stmt(self) -> ForStmt:
        tok = self.expect(TokenType.FOR)
        var_name = self.read_identifier()
        self.expect(TokenType.ASSIGN)
        start = self.parse_expression()
        self.expect(TokenType.TO)
        end = self.parse_expression()
        
        step = None
        if self.at(TokenType.STEP):
            self.advance()
            step = self.parse_expression()
        
        self.skip_newlines()
        body = []
        while not self.at(TokenType.NEXT):
            body.append(self.parse_statement())
            self.skip_newlines()
        
        self.expect(TokenType.NEXT)
        # Optional var name after NEXT
        if self.at(TokenType.IDENTIFIER):
            self.advance()
        
        return ForStmt(
            var_name=var_name, start=start, end=end,
            step=step, body=body, **self.loc(tok)
        )
    
    def parse_while_stmt(self) -> WhileStmt:
        tok = self.expect(TokenType.WHILE)
        cond = self.parse_expression()
        self.skip_newlines()
        
        body = []
        while not self.at(TokenType.WEND):
            body.append(self.parse_statement())
            self.skip_newlines()
        
        self.expect(TokenType.WEND)
        return WhileStmt(condition=cond, body=body, **self.loc(tok))
    
    def parse_goto_stmt(self) -> GotoStmt:
        tok = self.expect(TokenType.GOTO)
        target = self.read_identifier()
        return GotoStmt(target=target, **self.loc(tok))
    
    def parse_gosub_stmt(self) -> GosubStmt:
        tok = self.expect(TokenType.GOSUB)
        target = self.read_identifier()
        return GosubStmt(target=target, **self.loc(tok))
    
    def parse_return_stmt(self) -> ReturnStmt:
        tok = self.expect(TokenType.RETURN)
        value = None
        if not self.at(TokenType.NEWLINE, TokenType.EOF,
                       TokenType.RBRACE, TokenType.RBRACKET):
            value = self.parse_expression()
        return ReturnStmt(value=value, **self.loc(tok))
    
    def parse_label_stmt(self) -> LabelStmt:
        tok = self.expect(TokenType.LABEL)
        name = tok.value[1:]  # Strip '@'
        # Optional colon
        self.match(TokenType.COLON)
        return LabelStmt(name=name, **self.loc(tok))
    
    # ---- I/O ----
    
    def parse_print_stmt(self) -> PrintStmt:
        tok = self.expect(TokenType.PRINT)
        values = [self.parse_expression()]
        while self.at(TokenType.COMMA):
            self.advance()
            values.append(self.parse_expression())
        return PrintStmt(values=values, **self.loc(tok))
    
    def parse_input_stmt(self) -> InputStmt:
        tok = self.expect(TokenType.INPUT)
        prompt = None
        # Check for optional prompt string
        if self.at(TokenType.STRING_LIT) and self.peek().type == TokenType.COMMA:
            prompt_tok = self.advance()
            prompt = prompt_tok.value[1:-1]
            self.advance()  # comma
        variable = self.read_identifier()
        return InputStmt(prompt=prompt, variable=variable, **self.loc(tok))
    
    # ---- Debug / Trust ----
    
    def parse_halt_stmt(self) -> HaltStmt:
        tok = self.expect(TokenType.HALT)
        msg = None
        if self.at(TokenType.STRING_LIT):
            msg_tok = self.advance()
            msg = msg_tok.value[1:-1]
        return HaltStmt(message=msg, **self.loc(tok))
    
    def parse_inspect_stmt(self) -> InspectStmt:
        tok = self.expect(TokenType.INSPECT)
        target = self.read_identifier()
        return InspectStmt(target=target, **self.loc(tok))
    
    # ---- Assignment or Expression ----
    
    def parse_assignment_or_expr(self) -> ASTNode:
        """
        Parse either:
          ident = expr          → Assignment
          ident[expr] = expr    → Indexed assignment
          ident(args)           → Expression (call)
          ident.field           → Expression (dot)
        """
        tok = self.current
        
        # Look ahead to determine if this is assignment
        if (self.current.type == TokenType.IDENTIFIER and
            self.peek().type == TokenType.ASSIGN):
            name = self.read_identifier()
            self.advance()  # '='
            value = self.parse_expression()
            return Assignment(target=name, value=value, **self.loc(tok))
        
        if (self.current.type == TokenType.IDENTIFIER and
            self.peek().type == TokenType.LBRACKET):
            name = self.read_identifier()
            self.advance()  # '['
            index = self.parse_expression()
            self.expect(TokenType.RBRACKET)
            self.expect(TokenType.ASSIGN)
            value = self.parse_expression()
            return Assignment(
                target=name, index=index, value=value, **self.loc(tok)
            )
        
        # Fall through to expression
        expr = self.parse_expression()
        return expr
    
    # ============================================================
    #  VECTOR OPERATIONS
    # ============================================================
    
    def parse_vec_arith(self) -> VecArith:
        tok = self.advance()  # Op token
        a = self.parse_expression()
        self.expect(TokenType.COMMA)
        b = self.parse_expression()
        self.expect(TokenType.INTO)
        target = self.read_identifier()
        return VecArith(
            op=tok.value, operand_a=a, operand_b=b,
            target=target, **self.loc(tok)
        )
    
    def parse_vec_load(self) -> VecLoad:
        tok = self.expect(TokenType.VLOAD)
        target = self.read_identifier()
        self.expect(TokenType.FROM)
        source = self.parse_expression()
        return VecLoad(target=target, source=source, **self.loc(tok))
    
    def parse_vec_store(self) -> VecStore:
        tok = self.expect(TokenType.VSTORE)
        source = self.read_identifier()
        self.expect(TokenType.TO)
        target = self.parse_expression()
        return VecStore(source=source, target=target, **self.loc(tok))
    
    def parse_vec_reduce(self) -> VecReduce:
        tok = self.advance()
        source = self.parse_expression()
        self.expect(TokenType.INTO)
        target = self.read_identifier()
        return VecReduce(
            op=tok.value, source=source, target=target, **self.loc(tok)
        )
    
    # ============================================================
    #  COMMUNICATION
    # ============================================================
    
    def parse_send_stmt(self) -> SendStmt:
        tok = self.expect(TokenType.SEND)
        value = self.parse_expression()
        self.expect(TokenType.TO)
        target_module = self.read_identifier()
        target_channel = None
        if self.at(TokenType.DOT):
            self.advance()
            target_channel = self.read_identifier()
        return SendStmt(
            value=value, target_module=target_module,
            target_channel=target_channel, **self.loc(tok)
        )
    
    def parse_recv_stmt(self) -> RecvStmt:
        tok = self.expect(TokenType.RECV)
        variable = self.read_identifier()
        self.expect(TokenType.FROM)
        source_module = self.read_identifier()
        source_channel = None
        if self.at(TokenType.DOT):
            self.advance()
            source_channel = self.read_identifier()
        timeout = None
        if self.at(TokenType.TIMEOUT):
            self.advance()
            timeout = self.parse_expression()
        return RecvStmt(
            variable=variable, source_module=source_module,
            source_channel=source_channel, timeout=timeout,
            **self.loc(tok)
        )
    
    def parse_readport_stmt(self) -> ReadPortStmt:
        tok = self.expect(TokenType.READPORT)
        port = self.read_identifier()
        self.expect(TokenType.INTO)
        variable = self.read_identifier()
        return ReadPortStmt(port=port, variable=variable, **self.loc(tok))
    
    def parse_lock_stmt(self) -> LockStmt:
        tok = self.expect(TokenType.LOCK)
        target = self.read_identifier()
        self.skip_newlines()
        
        body = []
        while not self.at(TokenType.UNLOCK):
            body.append(self.parse_statement())
            self.skip_newlines()
        
        self.expect(TokenType.UNLOCK)
        self.read_identifier()  # Must match target (semantic check later)
        
        return LockStmt(target=target, body=body, **self.loc(tok))
    
    # ============================================================
    #  INLINE ASSEMBLY (BBC BASIC STYLE)
    # ============================================================
    
    def parse_asm_block(self) -> AsmBlock:
        """ASM [name] ... END ASM"""
        tok = self.expect(TokenType.ASM)
        
        name = None
        if self.at(TokenType.IDENTIFIER):
            name = self.read_identifier()
        
        self.skip_newlines()
        prev_asm = self.in_asm
        self.in_asm = True
        
        body = []
        while not self.at(TokenType.END_ASM):
            body.append(self.parse_asm_line())
            self.skip_newlines()
        
        self.expect(TokenType.END_ASM)
        self.in_asm = prev_asm
        
        return AsmBlock(name=name, body=body, **self.loc(tok))
    
    def parse_asm_inline(self) -> AsmInline:
        """[ ... ] — BBC BASIC style inline asm"""
        tok = self.expect(TokenType.LBRACKET)
        self.skip_newlines()
        prev_asm = self.in_asm
        self.in_asm = True
        
        body = []
        while not self.at(TokenType.RBRACKET):
            body.append(self.parse_asm_line())
            self.skip_newlines()
        
        self.expect(TokenType.RBRACKET)
        self.in_asm = prev_asm
        
        return AsmInline(body=body, **self.loc(tok))
    
    def parse_asm_line(self) -> ASTNode:
        """
        Parse a single line inside an asm block.
        Could be: asm instruction, label def, OPT, EQU,
        or an Orb statement (FOR, IF, etc — BBC BASIC mixing).
        """
        tok = self.current
        
        if tok.type == TokenType.COMMENT:
            return self.parse_comment()
        
        # ASM label definition: .name:
        if tok.type == TokenType.ASM_LABEL and tok.value.endswith(':'):
            return self.parse_asm_label_def()
        
        # EQU: .name EQU expr
        if (tok.type == TokenType.ASM_LABEL and
            not tok.value.endswith(':') and
            self.peek().type == TokenType.EQU):
            return self.parse_asm_equate()
        
        # OPT directive
        if tok.type == TokenType.OPT:
            return self.parse_asm_opt()
        
        # ASM instruction
        if tok.type in ASM_OPS:
            return self.parse_asm_instruction()
        
        # Orb statement inside asm (BBC BASIC style)
        return self.parse_statement()
    
    def parse_asm_instruction(self) -> AsmInstruction:
        tok = self.advance()  # op token
        operands = []
        
        # Parse operands until newline/EOF/comment
        if not self.at(TokenType.NEWLINE, TokenType.EOF, TokenType.COMMENT):
            operands.append(self.parse_asm_operand())
            while self.at(TokenType.COMMA):
                self.advance()
                operands.append(self.parse_asm_operand())
        
        return AsmInstruction(
            op=tok.value, operands=operands, **self.loc(tok)
        )
    
    def parse_asm_operand(self) -> AsmOperand:
        """Parse a single asm operand."""
        tok = self.current
        
        # Register: D0-D7, A0-A7
        if tok.type == TokenType.REGISTER:
            self.advance()
            return AsmOperand(kind="register", value=tok.value, **self.loc(tok))
        
        # Immediate: #value or #(expression)
        if tok.type == TokenType.IMMEDIATE:
            self.advance()
            return AsmOperand(kind="immediate", value=tok.value, **self.loc(tok))
        
        if tok.type == TokenType.HASH:
            self.advance()  # '#'
            self.expect(TokenType.LPAREN)
            expr = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return AsmOperand(
                kind="expr_immediate", expression=expr, **self.loc(tok)
            )
        
        # Bare numeric literals as immediates (no # prefix)
        # Common in orb42 assembly: ALUI D1, D0, 30
        if tok.type in (TokenType.INTEGER_LIT, TokenType.FLOAT_LIT, TokenType.HEX_LIT):
            self.advance()
            if tok.type == TokenType.HEX_LIT:
                val = int(tok.value, 16)
            elif tok.type == TokenType.FLOAT_LIT:
                val = float(tok.value)
            else:
                val = int(tok.value)
            return AsmOperand(kind="immediate", value=f"#{tok.value}", **self.loc(tok))
        
        # Indirect: [An] or indexed: [An, offset]
        if tok.type == TokenType.LBRACKET:
            self.advance()  # '['
            reg_tok = self.expect(TokenType.REGISTER)
            offset = None
            if self.at(TokenType.COMMA):
                self.advance()
                offset = self.parse_expression()
            self.expect(TokenType.RBRACKET)
            if offset:
                return AsmOperand(
                    kind="indexed", value=reg_tok.value,
                    offset=offset, **self.loc(tok)
                )
            else:
                return AsmOperand(
                    kind="indirect", value=reg_tok.value, **self.loc(tok)
                )
        
        # ASM label reference: .name (branch target)
        if tok.type == TokenType.ASM_LABEL:
            self.advance()
            name = tok.value  # e.g. ".loop" or ".loop:"
            if name.endswith(':'):
                name = name[:-1]
            return AsmOperand(kind="label_ref", value=name, **self.loc(tok))
        
        # Variable bridge: identifier (reads Orb variable)
        if tok.type == TokenType.IDENTIFIER:
            self.advance()
            return AsmOperand(kind="variable", value=tok.value, **self.loc(tok))
        
        # ALU sub-operation keywords used as operands in ALU instruction
        # e.g. ALU D0, D1, D2, XOR — the XOR is a keyword but acts as operand
        ALU_SUB_OPS = {
            TokenType.ADD, TokenType.SUB, TokenType.AND_ASM, TokenType.OR_ASM,
            TokenType.XOR, TokenType.NOT_ASM, TokenType.SHL, TokenType.SHR,
            TokenType.ASR, TokenType.CMP, TokenType.MUL, TokenType.MULH,
            TokenType.MIN_OP, TokenType.MAX_OP, TokenType.CLZ, TokenType.ABS_OP,
            TokenType.SEXT8, TokenType.SEXT16, TokenType.BREV, TokenType.BSWAP,
            TokenType.POPCNT, TokenType.SATADD, TokenType.SATSUB, TokenType.ROR,
        }
        if tok.type in ALU_SUB_OPS:
            self.advance()
            return AsmOperand(kind="variable", value=tok.value, **self.loc(tok))
        
        # SYS sub-commands: HALT, NOP, SETDP used as operands
        SYS_SUB_OPS = {TokenType.HLT, TokenType.NOP, TokenType.SETDP}
        if tok.type in SYS_SUB_OPS:
            self.advance()
            return AsmOperand(kind="variable", value=tok.value, **self.loc(tok))
        
        # Other keywords that can appear as operands in certain contexts
        if tok.type in (TokenType.HALT,):
            self.advance()
            return AsmOperand(kind="variable", value=tok.value, **self.loc(tok))
        
        self.error(f"Expected asm operand, got {tok.type.name}")
    
    def parse_asm_label_def(self) -> AsmLabelDef:
        tok = self.expect(TokenType.ASM_LABEL)
        name = tok.value[1:]  # Strip leading '.'
        if name.endswith(':'):
            name = name[:-1]
        return AsmLabelDef(name=name, **self.loc(tok))
    
    def parse_asm_opt(self) -> AsmOpt:
        tok = self.expect(TokenType.OPT)
        value = self.parse_expression()
        return AsmOpt(value=value, **self.loc(tok))
    
    def parse_asm_equate(self) -> AsmEquate:
        tok = self.expect(TokenType.ASM_LABEL)
        name = tok.value[1:]  # Strip '.'
        if name.endswith(':'):
            name = name[:-1]
        self.expect(TokenType.EQU)
        value = self.parse_expression()
        return AsmEquate(name=name, value=value, **self.loc(tok))
    
    # ============================================================
    #  EXPRESSIONS — PRECEDENCE CLIMBING
    # ============================================================
    
    def parse_expression(self) -> Expression:
        return self.parse_or_expr()
    
    def parse_or_expr(self) -> Expression:
        left = self.parse_and_expr()
        while self.at(TokenType.OR):
            tok = self.advance()
            right = self.parse_and_expr()
            left = BinaryOp(op="OR", left=left, right=right, **self.loc(tok))
        return left
    
    def parse_and_expr(self) -> Expression:
        left = self.parse_not_expr()
        while self.at(TokenType.AND):
            tok = self.advance()
            right = self.parse_not_expr()
            left = BinaryOp(op="AND", left=left, right=right, **self.loc(tok))
        return left
    
    def parse_not_expr(self) -> Expression:
        if self.at(TokenType.NOT):
            tok = self.advance()
            operand = self.parse_compare_expr()
            return UnaryOp(op="NOT", operand=operand, **self.loc(tok))
        return self.parse_compare_expr()
    
    def parse_compare_expr(self) -> Expression:
        left = self.parse_add_expr()
        comp_ops = {
            TokenType.EQ: "==", TokenType.NEQ: "!=",
            TokenType.LT: "<", TokenType.GT: ">",
            TokenType.LTE: "<=", TokenType.GTE: ">=",
        }
        while self.current.type in comp_ops:
            tok = self.advance()
            right = self.parse_add_expr()
            left = BinaryOp(
                op=comp_ops[tok.type], left=left, right=right,
                **self.loc(tok)
            )
        return left
    
    def parse_add_expr(self) -> Expression:
        left = self.parse_mul_expr()
        while self.at(TokenType.PLUS, TokenType.MINUS):
            tok = self.advance()
            right = self.parse_mul_expr()
            left = BinaryOp(
                op=tok.value, left=left, right=right, **self.loc(tok)
            )
        return left
    
    def parse_mul_expr(self) -> Expression:
        left = self.parse_unary_expr()
        while self.at(TokenType.STAR, TokenType.SLASH, TokenType.PERCENT):
            tok = self.advance()
            right = self.parse_unary_expr()
            left = BinaryOp(
                op=tok.value, left=left, right=right, **self.loc(tok)
            )
        return left
    
    def parse_unary_expr(self) -> Expression:
        if self.at(TokenType.MINUS):
            tok = self.advance()
            operand = self.parse_postfix_expr()
            return UnaryOp(op="-", operand=operand, **self.loc(tok))
        if self.at(TokenType.TILDE):
            tok = self.advance()
            operand = self.parse_postfix_expr()
            return UnaryOp(op="~", operand=operand, **self.loc(tok))
        return self.parse_postfix_expr()
    
    def parse_postfix_expr(self) -> Expression:
        expr = self.parse_primary()
        
        while True:
            if self.at(TokenType.LBRACKET) and not self.in_asm:
                # Array index: expr[index]
                self.advance()
                index = self.parse_expression()
                self.expect(TokenType.RBRACKET)
                expr = IndexExpr(
                    target=expr, index=index,
                    line=expr.line, col=expr.col
                )
            elif self.at(TokenType.DOT):
                # Dot access: expr.field
                self.advance()
                field_name = self.read_identifier()
                expr = DotExpr(
                    target=expr, field_name=field_name,
                    line=expr.line, col=expr.col
                )
            elif (self.at(TokenType.LPAREN) and
                  isinstance(expr, Identifier)):
                # Function call: name(args)
                self.advance()
                args = []
                if not self.at(TokenType.RPAREN):
                    args.append(self.parse_expression())
                    while self.at(TokenType.COMMA):
                        self.advance()
                        args.append(self.parse_expression())
                self.expect(TokenType.RPAREN)
                expr = CallExpr(
                    func_name=expr.name, args=args,
                    line=expr.line, col=expr.col
                )
            else:
                break
        
        return expr
    
    def parse_primary(self) -> Expression:
        tok = self.current
        
        # Integer literal
        if tok.type == TokenType.INTEGER_LIT:
            self.advance()
            return IntLiteral(value=int(tok.value), **self.loc(tok))
        
        # Float literal
        if tok.type == TokenType.FLOAT_LIT:
            self.advance()
            return FloatLiteral(value=float(tok.value), **self.loc(tok))
        
        # Hex literal
        if tok.type == TokenType.HEX_LIT:
            self.advance()
            return HexLiteral(
                value=int(tok.value, 16), raw=tok.value, **self.loc(tok)
            )
        
        # String literal
        if tok.type == TokenType.STRING_LIT:
            self.advance()
            return StringLiteral(value=tok.value[1:-1], **self.loc(tok))
        
        # Identifier
        if tok.type == TokenType.IDENTIFIER:
            self.advance()
            return Identifier(name=tok.value, **self.loc(tok))
        
        # Parenthesised expression
        if tok.type == TokenType.LPAREN:
            self.advance()
            expr = self.parse_expression()
            self.expect(TokenType.RPAREN)
            return expr
        
        # Vec literal: <expr, expr, ...>
        # Disambiguation: '<' at primary level with comma-separated
        # values followed by '>' is a vec literal, not comparison.
        if tok.type == TokenType.LT:
            return self.parse_vec_literal()
        
        # ASM label reference inside expression (for EQU values etc)
        if tok.type == TokenType.ASM_LABEL:
            self.advance()
            name = tok.value
            if name.endswith(':'):
                name = name[:-1]
            return Identifier(name=name, **self.loc(tok))
        
        self.error(f"Expected expression, got {tok.type.name}")
    
    def parse_vec_literal(self) -> VecLiteral:
        """Parse <expr, expr, ...> — vec literal.
        Elements parsed at add_expr level so > and < don't get consumed
        as comparison operators inside the literal."""
        tok = self.expect(TokenType.LT)
        elements = [self.parse_add_expr()]
        while self.at(TokenType.COMMA):
            self.advance()
            elements.append(self.parse_add_expr())
        self.expect(TokenType.GT)
        return VecLiteral(elements=elements, **self.loc(tok))


# ============================================================
#  KEYWORDS THAT CAN BE USED AS IDENTIFIERS
# ============================================================
# After DOT, AS (in EXPORT/PLACE), and in certain other contexts,
# keywords should be treated as identifiers.

KEYWORDS_AS_IDENT = {
    TokenType.INPUT, TokenType.OUTPUT if hasattr(TokenType, 'OUTPUT') else None,
    TokenType.INT, TokenType.FLOAT, TokenType.STRING, TokenType.VEC,
    TokenType.IN, TokenType.OUT, TokenType.INOUT,
    TokenType.ADD, TokenType.SUB, TokenType.MUL,
    TokenType.SET, TokenType.MAIN,
    TokenType.FROM, TokenType.TO, TokenType.INTO,
    TokenType.STEP, TokenType.NEXT,
} - {None}


# ============================================================
#  AST PRETTY PRINTER
# ============================================================

def dump_ast(node: ASTNode, indent: int = 0) -> str:
    """Pretty-print an AST tree for debugging."""
    pad = "  " * indent
    lines = []
    
    name = type(node).__name__
    
    # Get the key fields to display
    fields = {}
    for k, v in node.__dict__.items():
        if k in ('line', 'col'):
            continue
        if v is None or v == "" or v == [] or v == 0:
            continue
        fields[k] = v
    
    loc = f"L{node.line}:{node.col}"
    
    # Compact display for simple nodes
    if isinstance(node, (IntLiteral, FloatLiteral, HexLiteral)):
        lines.append(f"{pad}{name}({node.value}) [{loc}]")
    elif isinstance(node, StringLiteral):
        lines.append(f"{pad}{name}({node.value!r}) [{loc}]")
    elif isinstance(node, Identifier):
        lines.append(f"{pad}{name}({node.name}) [{loc}]")
    elif isinstance(node, AsmOperand):
        extra = f" val={node.value}" if node.value else ""
        lines.append(f"{pad}{name}({node.kind}{extra}) [{loc}]")
        if node.expression:
            lines.append(dump_ast(node.expression, indent + 1))
        if node.offset:
            lines.append(dump_ast(node.offset, indent + 1))
    else:
        # General node
        attrs = []
        children = []
        for k, v in fields.items():
            if isinstance(v, list) and v and isinstance(v[0], ASTNode):
                children.append((k, v))
            elif isinstance(v, ASTNode):
                children.append((k, [v]))
            elif isinstance(v, list) and v and isinstance(v[0], str):
                attrs.append(f"{k}={v}")
            else:
                if not isinstance(v, (list, dict)):
                    attrs.append(f"{k}={v!r}" if isinstance(v, str) else f"{k}={v}")
        
        attr_str = ", ".join(attrs)
        if attr_str:
            lines.append(f"{pad}{name}({attr_str}) [{loc}]")
        else:
            lines.append(f"{pad}{name} [{loc}]")
        
        for child_name, child_list in children:
            lines.append(f"{pad}  {child_name}:")
            for child in child_list:
                lines.append(dump_ast(child, indent + 2))
    
    return "\n".join(lines)


# ============================================================
#  CONVENIENCE
# ============================================================

def parse_source(source: str, filename: str = "<input>") -> Program:
    """Lex and parse source code, return AST."""
    lexer = Lexer(source, filename)
    tokens = lexer.tokenise()
    parser = Parser(tokens)
    return parser.parse()


# ============================================================
#  SELF-TEST
# ============================================================

if __name__ == "__main__":
    test_source = r"""
// Simple Orb program with board and modules

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
    
    // BBC BASIC inline asm — orb42_core_v2
    [
        ALU D0, D0, D0, XOR
        MOV D1, gain
        ALUI D2, D0, 8
        ALU D1, D1, D2, ADD
        .loop:
        ALUI D2, D2, #-1
        BNE D2, D0, .loop
        MOV gain, D1
    ]
    
    // Named asm block with two-pass
    ASM fast_multiply
        .SCALE EQU 256
        FOR pass = 0 TO 2 STEP 2
            OPT pass
            .start:
            LOADW D1, [A0, 0]
            ALU D1, D1, D3, MUL
            STOREW D1, [A1, 4]
            ADDQ A0, 4
            ADDQ A1, 4
            BNE A0, A2, .start
        NEXT pass
    END ASM
}
END MODULE

MODULE logger MESSAGE
{
    DIM msg AS STRING = ""
    RECV msg FROM amplifier.status TIMEOUT 500
    PRINT "Log: ", msg
    SEND msg TO display
    HALT "checkpoint"
}
END MODULE

BOARD main_board
    PLACE amplifier AS amp1
    PLACE amplifier AS amp2 AT 100, 200
    PLACE logger    AS log
    PLACE mixer     AS mix
    
    WIRE amp1.output TO mix.input_a
    WIRE amp2.output TO mix.input_b
    
    SET amp1.gain = 2.5
    SET amp2.gain = 0.8
    
    ROUTE amp1.overflow TO log.alert
    SHARE gain_state BETWEEN amp1, amp2, mix
    
    PROBE amp1.output AS "Amp 1 Output"
    PROBE mix.output  AS "Final Mix"
    
    EXPORT mix.output AS master_out
END BOARD

IMPORT "modules/effects"
"""
    
    print("=" * 60)
    print("  ORB LANGUAGE PARSER — TEST RUN")
    print("=" * 60)
    print()
    
    try:
        ast = parse_source(test_source, "test.orb")
        print(dump_ast(ast))
        
        # Count node types
        def count_nodes(node, counts=None):
            if counts is None:
                counts = {}
            name = type(node).__name__
            counts[name] = counts.get(name, 0) + 1
            for v in node.__dict__.values():
                if isinstance(v, ASTNode):
                    count_nodes(v, counts)
                elif isinstance(v, list):
                    for item in v:
                        if isinstance(item, ASTNode):
                            count_nodes(item, counts)
            return counts
        
        counts = count_nodes(ast)
        print()
        print("Node type counts:")
        for name, count in sorted(counts.items(), key=lambda x: -x[1]):
            print(f"  {name:<20} {count}")
        print(f"\n  Total: {sum(counts.values())} nodes")
        
    except (ParseError, LexerError) as e:
        print(f"ERROR: {e}")
