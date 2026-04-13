"""
ORB LANGUAGE — LEXER / TOKENISER
=================================
Converts raw source text into a stream of typed tokens.
Designed for interpreted execution with live geometric view.

Token stream preserves source locations (line, column) on every
token so the geometric renderer can map visuals back to source.
"""

from enum import Enum, auto
from dataclasses import dataclass, field
from typing import List, Optional, Iterator
import re


# ============================================================
#  TOKEN TYPES
# ============================================================

class TokenType(Enum):
    # ---- Literals ----
    INTEGER_LIT   = auto()
    FLOAT_LIT     = auto()
    HEX_LIT       = auto()
    STRING_LIT    = auto()
    
    # ---- Identifiers & Labels ----
    IDENTIFIER    = auto()
    LABEL         = auto()    # @name
    ASM_LABEL     = auto()    # .name:
    REGISTER      = auto()    # R0..R31
    IMMEDIATE     = auto()    # #value
    
    # ---- Keywords: Structure ----
    MODULE        = auto()
    END_MODULE    = auto()
    PORTS         = auto()
    END_PORTS     = auto()
    SHARED_BLOCK  = auto()    # SHARED (block opener)
    END_SHARED    = auto()
    
    # ---- Keywords: Comm Modes ----
    DATAFLOW      = auto()
    MESSAGE       = auto()
    KW_SHARED     = auto()    # SHARED (comm mode selector)
    
    # ---- Keywords: Port Direction ----
    IN            = auto()
    OUT           = auto()
    INOUT         = auto()
    
    # ---- Keywords: Declarations ----
    DIM           = auto()
    CONST         = auto()
    AS            = auto()
    
    # ---- Keywords: Types ----
    INT           = auto()
    FLOAT         = auto()
    STRING        = auto()
    VEC           = auto()
    
    # ---- Keywords: Control Flow ----
    IF            = auto()
    THEN          = auto()
    ELIF          = auto()
    ELSE          = auto()
    END_IF        = auto()
    FOR           = auto()
    TO            = auto()
    STEP          = auto()
    NEXT          = auto()
    WHILE         = auto()
    WEND          = auto()
    GOTO          = auto()
    GOSUB         = auto()
    RETURN        = auto()
    
    # ---- Keywords: I/O ----
    PRINT         = auto()
    INPUT         = auto()
    
    # ---- Keywords: Debug / Trust ----
    HALT          = auto()
    INSPECT       = auto()
    
    # ---- Keywords: Vector Ops (Verb Level) ----
    VADD          = auto()
    VSUB          = auto()
    VMUL          = auto()
    VDIV          = auto()
    VMADD         = auto()
    VLOAD         = auto()
    VSTORE        = auto()
    VSUM          = auto()
    VDOT          = auto()
    VMIN          = auto()
    VMAX          = auto()
    INTO          = auto()
    FROM          = auto()
    
    # ---- Keywords: ASM Block ----
    ASM           = auto()
    END_ASM       = auto()
    
    # ---- Keywords: ASM — Base ISA (orb42_core_v2) ----
    LOADW         = auto()    # LOAD.W  — load word via address register
    STOREW        = auto()    # STORE.W — store word via address register
    LOADDP        = auto()    # LOAD.DP — load word via data page
    STOREDP       = auto()    # STORE.DP — store word via data page
    MOV           = auto()    # Register-to-register (D↔A crossing)
    ALU_OP        = auto()    # 3-register ALU: ALU Dd, Ds, Dt, <aluop>
    ALUI          = auto()    # Register-immediate ALU: ALUI Dd, Ds, imm
    LUI           = auto()    # Load upper immediate
    BEQ           = auto()    # Branch if equal (compare two registers)
    BNE           = auto()    # Branch if not equal
    BLT           = auto()    # Branch if less than (signed)
    BGE           = auto()    # Branch if greater or equal (signed)
    JAL           = auto()    # Jump and link
    LEA           = auto()    # Load effective address
    ADDQ          = auto()    # Quick add to address register
    NOP           = auto()
    HLT           = auto()    # SYS HALT
    
    # ---- Keywords: ASM — ALU sub-operations (24 ops) ----
    ADD           = auto()
    SUB           = auto()
    AND_ASM       = auto()    # AND (asm bitwise)
    OR_ASM        = auto()    # OR  (asm bitwise)
    XOR           = auto()
    NOT_ASM       = auto()    # NOT (asm bitwise)
    SHL           = auto()
    SHR           = auto()
    ASR           = auto()    # Arithmetic shift right
    CMP           = auto()
    MUL           = auto()
    MULH          = auto()    # Multiply high word
    MIN_OP        = auto()    # MIN (asm)
    MAX_OP        = auto()    # MAX (asm)
    CLZ           = auto()    # Count leading zeros
    ABS_OP        = auto()    # Absolute value
    SEXT8         = auto()    # Sign extend byte
    SEXT16        = auto()    # Sign extend halfword
    BREV          = auto()    # Bit reverse
    BSWAP         = auto()    # Byte swap
    POPCNT        = auto()    # Population count
    SATADD        = auto()    # Saturating add
    SATSUB        = auto()    # Saturating sub
    ROR           = auto()    # Rotate right
    
    # ---- Keywords: ASM — Coprocessor (0xF prefix) ----
    SYS           = auto()    # SYS NOP / SYS HALT / SYS SETDP
    SETDP         = auto()    # Set data page register
    SINCOS        = auto()    # CORDIC: angle → cos, sin
    ATAN2         = auto()    # CORDIC: x,y → magnitude, angle
    PSEL          = auto()    # Phaser core select
    PMAC          = auto()    # Phaser MAC fire + read
    PCLR          = auto()    # Phaser MAC clear
    PLUT          = auto()    # Phaser LUT write
    PDRIFT        = auto()    # Read composite phaser drift XOR
    PWAVE         = auto()    # Read phaser waveform output
    CDIV          = auto()    # Coprocessor divider: D0=Dd/Ds, D1=Dd%Ds
    RSQRT         = auto()    # Newton-Raphson: D0 = 1/√Dd
    VLOADL        = auto()    # Vector lane load A bank
    VLOADB_ASM    = auto()    # Vector lane load B bank
    VOP           = auto()    # Vector operation execute
    VREAD         = auto()    # Vector read result lane
    VDOTRD        = auto()    # Vector read dot product accumulator
    
    # ---- Keywords: ASM — Stack (available but not in core ISA) ----
    PUSH          = auto()
    POP           = auto()
    
    # ---- Keywords: ASM Directives (BBC BASIC style) ----
    OPT           = auto()
    EQU           = auto()
    
    # ---- Keywords: Communication ----
    SEND          = auto()
    RECV          = auto()
    WIRE          = auto()
    READPORT      = auto()
    LOCK          = auto()
    UNLOCK        = auto()
    TIMEOUT       = auto()
    
    # ---- Keywords: Board / Linking ----
    BOARD         = auto()
    END_BOARD     = auto()
    PLACE         = auto()
    ROUTE         = auto()
    SHARE         = auto()
    BETWEEN       = auto()
    SET           = auto()
    PROBE         = auto()
    EXPORT        = auto()
    IMPORT        = auto()
    MAIN          = auto()
    KW_AT         = auto()    # AT (position hint)
    
    # ---- Operators ----
    PLUS          = auto()    # +
    MINUS         = auto()    # -
    STAR          = auto()    # *
    SLASH         = auto()    # /
    PERCENT       = auto()    # %
    TILDE         = auto()    # ~
    ASSIGN        = auto()    # =
    EQ            = auto()    # ==
    NEQ           = auto()    # !=
    LT            = auto()    # <
    GT            = auto()    # >
    LTE           = auto()    # <=
    GTE           = auto()    # >=
    
    # ---- Logical (keyword-style) ----
    AND           = auto()
    OR            = auto()
    NOT           = auto()
    
    # ---- Delimiters ----
    LPAREN        = auto()    # (
    RPAREN        = auto()    # )
    LBRACKET      = auto()    # [
    RBRACKET      = auto()    # ]
    LBRACE        = auto()    # {
    RBRACE        = auto()    # }
    LANGLE        = auto()    # < (vec literal context)
    RANGLE        = auto()    # > (vec literal context)
    COMMA         = auto()    # ,
    DOT           = auto()    # .
    COLON         = auto()    # :
    HASH          = auto()    # #
    AT            = auto()    # @
    
    # ---- Structural ----
    NEWLINE       = auto()
    EOF           = auto()
    
    # ---- Comments (preserved for geometric view) ----
    COMMENT       = auto()


# ============================================================
#  TOKEN
# ============================================================

@dataclass
class Token:
    type: TokenType
    value: str
    line: int
    col: int
    # Source span for geometric mapping
    length: int = 0
    
    def __repr__(self):
        val = self.value if len(self.value) <= 20 else self.value[:17] + "..."
        return f"Token({self.type.name}, {val!r}, L{self.line}:{self.col})"


# ============================================================
#  KEYWORD TABLES
# ============================================================

# Two-word keywords must be checked before single-word
COMPOUND_KEYWORDS = {
    ("END", "MODULE"):  TokenType.END_MODULE,
    ("END", "PORTS"):   TokenType.END_PORTS,
    ("END", "SHARED"):  TokenType.END_SHARED,
    ("END", "IF"):      TokenType.END_IF,
    ("END", "ASM"):     TokenType.END_ASM,
    ("END", "BOARD"):   TokenType.END_BOARD,
}

KEYWORDS = {
    # Structure
    "MODULE":    TokenType.MODULE,
    "PORTS":     TokenType.PORTS,
    
    # Comm modes (context-sensitive: after MODULE = mode, standalone = block)
    "DATAFLOW":  TokenType.DATAFLOW,
    "MESSAGE":   TokenType.MESSAGE,
    
    # Port direction
    "IN":        TokenType.IN,
    "OUT":       TokenType.OUT,
    "INOUT":     TokenType.INOUT,
    
    # Declarations
    "DIM":       TokenType.DIM,
    "CONST":     TokenType.CONST,
    "AS":        TokenType.AS,
    
    # Types
    "INT":       TokenType.INT,
    "FLOAT":     TokenType.FLOAT,
    "STRING":    TokenType.STRING,
    "VEC":       TokenType.VEC,
    
    # Control flow
    "IF":        TokenType.IF,
    "THEN":      TokenType.THEN,
    "ELIF":      TokenType.ELIF,
    "ELSE":      TokenType.ELSE,
    "FOR":       TokenType.FOR,
    "TO":        TokenType.TO,
    "STEP":      TokenType.STEP,
    "NEXT":      TokenType.NEXT,
    "WHILE":     TokenType.WHILE,
    "WEND":      TokenType.WEND,
    "GOTO":      TokenType.GOTO,
    "GOSUB":     TokenType.GOSUB,
    "RETURN":    TokenType.RETURN,
    
    # I/O
    "PRINT":     TokenType.PRINT,
    "INPUT":     TokenType.INPUT,
    
    # Debug / Trust
    "HALT":      TokenType.HALT,
    "INSPECT":   TokenType.INSPECT,
    
    # Vector ops (verb level)
    "VADD":      TokenType.VADD,
    "VSUB":      TokenType.VSUB,
    "VMUL":      TokenType.VMUL,
    "VDIV":      TokenType.VDIV,
    "VMADD":     TokenType.VMADD,
    "VLOAD":     TokenType.VLOAD,
    "VSTORE":    TokenType.VSTORE,
    "VSUM":      TokenType.VSUM,
    "VDOT":      TokenType.VDOT,
    "VMIN":      TokenType.VMIN,
    "VMAX":      TokenType.VMAX,
    "INTO":      TokenType.INTO,
    "FROM":      TokenType.FROM,
    
    # ASM
    "ASM":       TokenType.ASM,
    
    # ASM — Base ISA (orb42_core_v2)
    "LOADW":     TokenType.LOADW,
    "STOREW":    TokenType.STOREW,
    "LOADDP":    TokenType.LOADDP,
    "STOREDP":   TokenType.STOREDP,
    "MOV":       TokenType.MOV,
    "ALU":       TokenType.ALU_OP,
    "ALUI":      TokenType.ALUI,
    "LUI":       TokenType.LUI,
    "BEQ":       TokenType.BEQ,
    "BNE":       TokenType.BNE,
    "BLT":       TokenType.BLT,
    "BGE":       TokenType.BGE,
    "JAL":       TokenType.JAL,
    "LEA":       TokenType.LEA,
    "ADDQ":      TokenType.ADDQ,
    "NOP":       TokenType.NOP,
    "HLT":       TokenType.HLT,
    # ASM — ALU sub-operations
    "ADD":       TokenType.ADD,
    "SUB":       TokenType.SUB,
    "XOR":       TokenType.XOR,
    "SHL":       TokenType.SHL,
    "SHR":       TokenType.SHR,
    "ASR":       TokenType.ASR,
    "CMP":       TokenType.CMP,
    "MUL":       TokenType.MUL,
    "MULH":      TokenType.MULH,
    "CLZ":       TokenType.CLZ,
    "SEXT8":     TokenType.SEXT8,
    "SEXT16":    TokenType.SEXT16,
    "BREV":      TokenType.BREV,
    "BSWAP":     TokenType.BSWAP,
    "POPCNT":    TokenType.POPCNT,
    "SATADD":    TokenType.SATADD,
    "SATSUB":    TokenType.SATSUB,
    "ROR":       TokenType.ROR,
    # ASM — Coprocessor
    "SYS":       TokenType.SYS,
    "SETDP":     TokenType.SETDP,
    "SINCOS":    TokenType.SINCOS,
    "ATAN2":     TokenType.ATAN2,
    "PSEL":      TokenType.PSEL,
    "PMAC":      TokenType.PMAC,
    "PCLR":      TokenType.PCLR,
    "PLUT":      TokenType.PLUT,
    "PDRIFT":    TokenType.PDRIFT,
    "PWAVE":     TokenType.PWAVE,
    "CDIV":      TokenType.CDIV,
    "RSQRT":     TokenType.RSQRT,
    "VLOADL":    TokenType.VLOADL,
    "VLOADB":    TokenType.VLOADB_ASM,
    "VOP":       TokenType.VOP,
    "VREAD":     TokenType.VREAD,
    "VDOTRD":    TokenType.VDOTRD,
    "PUSH":      TokenType.PUSH,
    "POP":       TokenType.POP,
    # ASM directives
    "OPT":       TokenType.OPT,
    "EQU":       TokenType.EQU,
    
    # Communication
    "SEND":      TokenType.SEND,
    "RECV":      TokenType.RECV,
    "WIRE":      TokenType.WIRE,
    "READPORT":  TokenType.READPORT,
    "LOCK":      TokenType.LOCK,
    "UNLOCK":    TokenType.UNLOCK,
    "TIMEOUT":   TokenType.TIMEOUT,
    
    # Board / Linking
    "BOARD":     TokenType.BOARD,
    "PLACE":     TokenType.PLACE,
    "ROUTE":     TokenType.ROUTE,
    "SHARE":     TokenType.SHARE,
    "BETWEEN":   TokenType.BETWEEN,
    "SET":       TokenType.SET,
    "PROBE":     TokenType.PROBE,
    "EXPORT":    TokenType.EXPORT,
    "IMPORT":    TokenType.IMPORT,
    "MAIN":      TokenType.MAIN,
    "AT":        TokenType.KW_AT,
    
    # Logical operators (keyword style)
    "AND":       TokenType.AND,
    "OR":        TokenType.OR,
    "NOT":       TokenType.NOT,
    
    # SHARED is context-sensitive
    "SHARED":    TokenType.SHARED_BLOCK,
}

# Inside ASM blocks, AND/OR/NOT become bitwise ops,
# and MIN/MAX/ABS become ALU ops
ASM_OVERRIDE = {
    "AND":  TokenType.AND_ASM,
    "OR":   TokenType.OR_ASM,
    "NOT":  TokenType.NOT_ASM,
    "MIN":  TokenType.MIN_OP,
    "MAX":  TokenType.MAX_OP,
    "ABS":  TokenType.ABS_OP,
}


# ============================================================
#  LEXER ERRORS
# ============================================================

class LexerError(Exception):
    def __init__(self, message: str, line: int, col: int):
        self.line = line
        self.col = col
        super().__init__(f"Lexer error at L{line}:{col}: {message}")


# ============================================================
#  LEXER
# ============================================================

class Lexer:
    """
    Tokenises Orb language source into a stream of Tokens.
    
    Preserves source positions on every token for geometric mapping.
    Comments are preserved (not discarded) so the visual view can 
    render them as annotation nodes.
    
    Usage:
        lexer = Lexer(source_code)
        tokens = lexer.tokenise()
        # or iterate:
        for token in lexer:
            ...
    """
    
    def __init__(self, source: str, filename: str = "<input>"):
        self.source = source
        self.filename = filename
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens: List[Token] = []
        self.in_asm_block = False    # Track ASM context for AND/OR/NOT and .labels
        self._in_asm_keyword = False # Inside ASM...END ASM (not inline brackets)
        self._prev_keyword = None   # Track context for SHARED ambiguity
        self._last_sig_type = None  # Last non-NEWLINE token type (for [ disambiguation)
        self._newline_since_sig = True  # Was there a newline since last significant token?
        self._bracket_asm_depth = 0 # Track nested [ ] for inline asm
    
    # ---- Character access ----
    
    @property
    def current(self) -> str:
        if self.pos >= len(self.source):
            return '\0'
        return self.source[self.pos]
    
    def peek(self, offset: int = 1) -> str:
        pos = self.pos + offset
        if pos >= len(self.source):
            return '\0'
        return self.source[pos]
    
    def advance(self) -> str:
        ch = self.current
        self.pos += 1
        if ch == '\n':
            self.line += 1
            self.col = 1
        else:
            self.col += 1
        return ch
    
    def match(self, expected: str) -> bool:
        if self.current == expected:
            self.advance()
            return True
        return False
    
    # ---- Token construction ----
    
    def make_token(self, ttype: TokenType, value: str,
                   line: int, col: int) -> Token:
        tok = Token(
            type=ttype,
            value=value,
            line=line,
            col=col,
            length=len(value)
        )
        
        # Track ASM block state (ASM ... END ASM)
        if ttype == TokenType.ASM:
            self._in_asm_keyword = True
            self.in_asm_block = True
        elif ttype == TokenType.END_ASM:
            self._in_asm_keyword = False
            self.in_asm_block = (self._bracket_asm_depth > 0)
        
        # Track inline asm brackets: [ at statement level = asm
        # A [ is array indexing only if it immediately follows an
        # expression-ending token with NO newline in between.
        # Skip bracket tracking entirely if inside ASM...END ASM
        # (where [ is always indirect addressing, not inline asm)
        if ttype == TokenType.LBRACKET and not self._in_asm_keyword:
            is_array_index = (
                not self._newline_since_sig and
                self._last_sig_type in {
                    TokenType.IDENTIFIER, TokenType.REGISTER,
                    TokenType.RBRACKET, TokenType.RPAREN,
                    TokenType.INTEGER_LIT, TokenType.FLOAT_LIT,
                    TokenType.HEX_LIT, TokenType.STRING_LIT,
                    TokenType.VEC,   # VEC[8] type specifier
                }
            )
            if not is_array_index:
                self._bracket_asm_depth += 1
                self.in_asm_block = True
        elif (ttype == TokenType.RBRACKET and
              self._bracket_asm_depth > 0 and
              not self._in_asm_keyword):
            self._bracket_asm_depth -= 1
            if self._bracket_asm_depth == 0:
                self.in_asm_block = False
        
        # Track last significant token and newline gap
        if ttype == TokenType.NEWLINE:
            self._newline_since_sig = True
        elif ttype != TokenType.COMMENT:
            self._last_sig_type = ttype
            self._newline_since_sig = False
        
        return tok
    
    def error(self, message: str):
        raise LexerError(message, self.line, self.col)
    
    # ---- Whitespace & Comments ----
    
    def skip_whitespace(self):
        """Skip spaces and tabs (NOT newlines — they're tokens)."""
        while self.pos < len(self.source) and self.current in (' ', '\t', '\r'):
            self.advance()
    
    def read_comment_rem(self) -> Token:
        """Read a REM comment to end of line."""
        start_line, start_col = self.line, self.col
        buf = []
        # consume 'REM'
        for _ in range(3):
            buf.append(self.advance())
        # consume rest of line
        while self.pos < len(self.source) and self.current != '\n':
            buf.append(self.advance())
        return self.make_token(TokenType.COMMENT, ''.join(buf),
                               start_line, start_col)
    
    def read_comment_slash(self) -> Token:
        """Read a // comment to end of line."""
        start_line, start_col = self.line, self.col
        buf = []
        # consume '//'
        buf.append(self.advance())
        buf.append(self.advance())
        while self.pos < len(self.source) and self.current != '\n':
            buf.append(self.advance())
        return self.make_token(TokenType.COMMENT, ''.join(buf),
                               start_line, start_col)
    
    # ---- Literal Readers ----
    
    def read_number(self) -> Token:
        """Read integer, float, or hex literal."""
        start_line, start_col = self.line, self.col
        buf = []
        
        # Check for hex: 0x...
        if self.current == '0' and self.peek() in ('x', 'X'):
            buf.append(self.advance())  # '0'
            buf.append(self.advance())  # 'x'
            if not self._is_hex_digit(self.current):
                self.error("Expected hex digit after '0x'")
            while self._is_hex_digit(self.current):
                buf.append(self.advance())
            return self.make_token(TokenType.HEX_LIT, ''.join(buf),
                                   start_line, start_col)
        
        # Integer or float
        while self.current.isdigit():
            buf.append(self.advance())
        
        if self.current == '.' and self.peek().isdigit():
            buf.append(self.advance())  # '.'
            while self.current.isdigit():
                buf.append(self.advance())
            return self.make_token(TokenType.FLOAT_LIT, ''.join(buf),
                                   start_line, start_col)
        
        return self.make_token(TokenType.INTEGER_LIT, ''.join(buf),
                               start_line, start_col)
    
    def read_string(self) -> Token:
        """Read a double-quoted string literal."""
        start_line, start_col = self.line, self.col
        self.advance()  # opening quote
        buf = []
        while self.pos < len(self.source):
            ch = self.current
            if ch == '"':
                self.advance()  # closing quote
                return self.make_token(
                    TokenType.STRING_LIT,
                    '"' + ''.join(buf) + '"',
                    start_line, start_col
                )
            if ch == '\\':
                self.advance()
                esc = self.advance()
                escape_map = {'n': '\n', 't': '\t', '\\': '\\', '"': '"'}
                buf.append(escape_map.get(esc, '\\' + esc))
            elif ch == '\n':
                self.error("Unterminated string literal")
            else:
                buf.append(self.advance())
        self.error("Unterminated string literal at end of file")
    
    def read_identifier_or_keyword(self) -> Token:
        """
        Read an identifier or keyword.
        Handles compound keywords (END MODULE, END IF, etc.)
        and context-sensitive tokens (SHARED, AND/OR/NOT in ASM).
        """
        start_line, start_col = self.line, self.col
        buf = []
        while self.pos < len(self.source) and (
            self.current.isalnum() or self.current == '_'
        ):
            buf.append(self.advance())
        
        word = ''.join(buf)
        upper = word.upper()
        
        # Check for compound keywords: END MODULE, END IF, etc.
        if upper == "END":
            # Peek ahead past whitespace for second word
            save_pos, save_line, save_col = self.pos, self.line, self.col
            self.skip_whitespace()
            if self.pos < len(self.source) and self.current.isalpha():
                second_start = self.pos
                second_buf = []
                while self.pos < len(self.source) and (
                    self.current.isalnum() or self.current == '_'
                ):
                    second_buf.append(self.advance())
                second = ''.join(second_buf).upper()
                compound = (upper, second)
                if compound in COMPOUND_KEYWORDS:
                    full = word + ' ' + ''.join(second_buf)
                    return self.make_token(
                        COMPOUND_KEYWORDS[compound], full,
                        start_line, start_col
                    )
                # Not a compound — backtrack
                self.pos = save_pos
                self.line = save_line
                self.col = save_col
            else:
                self.pos = save_pos
                self.line = save_line
                self.col = save_col
        
        # Check for register: D0..D7 or A0..A7
        if len(upper) == 2 and upper[0] in ('D', 'A') and upper[1].isdigit():
            reg_num = int(upper[1])
            if 0 <= reg_num <= 7:
                return self.make_token(TokenType.REGISTER, upper,
                                       start_line, start_col)
        
        # ASM context: AND/OR/NOT become bitwise ops
        if self.in_asm_block and upper in ASM_OVERRIDE:
            return self.make_token(ASM_OVERRIDE[upper], upper,
                                   start_line, start_col)
        
        # Context-sensitive: SHARED after MODULE = comm mode
        if upper == "SHARED":
            if self._prev_keyword == TokenType.MODULE:
                tok = self.make_token(TokenType.KW_SHARED, upper,
                                      start_line, start_col)
                self._prev_keyword = tok.type
                return tok
        
        # Regular keyword lookup
        if upper in KEYWORDS:
            tok = self.make_token(KEYWORDS[upper], upper,
                                  start_line, start_col)
            self._prev_keyword = tok.type
            return tok
        
        # Plain identifier (case-preserved)
        return self.make_token(TokenType.IDENTIFIER, word,
                               start_line, start_col)
    
    def read_label(self) -> Token:
        """Read @label_name — a flow anchor point."""
        start_line, start_col = self.line, self.col
        self.advance()  # '@'
        if not self.current.isalpha() and self.current != '_':
            self.error("Expected identifier after '@'")
        buf = []
        while self.pos < len(self.source) and (
            self.current.isalnum() or self.current == '_'
        ):
            buf.append(self.advance())
        return self.make_token(TokenType.LABEL, '@' + ''.join(buf),
                               start_line, start_col)
    
    def read_immediate(self) -> Token:
        """
        Read #value — an immediate operand.
        
        Forms:
          #42        → IMMEDIATE token with value '#42'
          #0xFF      → IMMEDIATE token with value '#0xFF'
          #-3.14     → IMMEDIATE token with value '#-3.14'
          #(expr)    → HASH token (parser handles the expression)
        
        The #(expression) form is the BBC BASIC-style expression
        immediate — the Orb expression inside parens is evaluated
        and the result becomes the immediate value.
        """
        start_line, start_col = self.line, self.col
        
        # #(expression) — return just HASH, parser handles the rest
        if self.peek() == '(':
            self.advance()  # consume '#'
            return self.make_token(TokenType.HASH, '#',
                                   start_line, start_col)
        
        self.advance()  # '#'
        if not (self.current.isdigit() or self.current == '-'):
            self.error("Expected number or '(' after '#'")
        buf = ['#']
        if self.current == '-':
            buf.append(self.advance())
        # Could be hex
        if self.current == '0' and self.peek() in ('x', 'X'):
            buf.append(self.advance())
            buf.append(self.advance())
            while self._is_hex_digit(self.current):
                buf.append(self.advance())
        else:
            while self.current.isdigit():
                buf.append(self.advance())
            if self.current == '.' and self.peek().isdigit():
                buf.append(self.advance())
                while self.current.isdigit():
                    buf.append(self.advance())
        return self.make_token(TokenType.IMMEDIATE, ''.join(buf),
                               start_line, start_col)
    
    def read_asm_label(self) -> Token:
        """
        Read .label: or .label — an assembly-level label.
        
        With colon:    .loop:    → ASM_LABEL (definition)
        Without colon: .loop     → ASM_LABEL (reference, no colon in value)
        """
        start_line, start_col = self.line, self.col
        self.advance()  # '.'
        buf = ['.']
        while self.pos < len(self.source) and (
            self.current.isalnum() or self.current == '_'
        ):
            buf.append(self.advance())
        # Colon makes it a definition; absence makes it a reference
        if self.current == ':':
            buf.append(self.advance())
        return self.make_token(TokenType.ASM_LABEL, ''.join(buf),
                               start_line, start_col)
    
    # ---- Utilities ----
    
    @staticmethod
    def _is_hex_digit(ch: str) -> bool:
        return ch in '0123456789abcdefABCDEF'
    
    # ---- Main Tokenise Loop ----
    
    def _next_token(self) -> Optional[Token]:
        """Extract the next token from source."""
        self.skip_whitespace()
        
        if self.pos >= len(self.source):
            return self.make_token(TokenType.EOF, '', self.line, self.col)
        
        ch = self.current
        start_line, start_col = self.line, self.col
        
        # ---- Newline ----
        if ch == '\n':
            self.advance()
            return self.make_token(TokenType.NEWLINE, '\\n',
                                   start_line, start_col)
        
        # ---- Comments ----
        if ch == '/' and self.peek() == '/':
            return self.read_comment_slash()
        
        # REM comment (only at word boundary)
        if (ch in ('R', 'r') and
            self.source[self.pos:self.pos+3].upper() == 'REM' and
            (self.pos + 3 >= len(self.source) or
             not self.source[self.pos+3].isalnum())):
            return self.read_comment_rem()
        
        # ---- String literals ----
        if ch == '"':
            return self.read_string()
        
        # ---- Number literals ----
        if ch.isdigit():
            return self.read_number()
        
        # ---- Identifiers / Keywords ----
        if ch.isalpha() or ch == '_':
            return self.read_identifier_or_keyword()
        
        # ---- Labels: @name ----
        if ch == '@':
            return self.read_label()
        
        # ---- Immediate: #value or #(expression) ----
        # Only in ASM context (ASM...END ASM or inline [...])
        if ch == '#' and self.in_asm_block and (
            self.peek().isdigit() or self.peek() == '-' or self.peek() == '('
        ):
            return self.read_immediate()
        
        # ---- ASM labels: .name: or .name (reference) ----
        # Only in ASM context — otherwise '.' is DOT for module.port access
        if ch == '.' and self.in_asm_block and self.peek().isalpha():
            return self.read_asm_label()
        
        # ---- Two-character operators ----
        if ch == '=' and self.peek() == '=':
            self.advance(); self.advance()
            return self.make_token(TokenType.EQ, '==',
                                   start_line, start_col)
        if ch == '!' and self.peek() == '=':
            self.advance(); self.advance()
            return self.make_token(TokenType.NEQ, '!=',
                                   start_line, start_col)
        if ch == '<' and self.peek() == '=':
            self.advance(); self.advance()
            return self.make_token(TokenType.LTE, '<=',
                                   start_line, start_col)
        if ch == '>' and self.peek() == '=':
            self.advance(); self.advance()
            return self.make_token(TokenType.GTE, '>=',
                                   start_line, start_col)
        
        # ---- Single-character tokens ----
        single_map = {
            '+': TokenType.PLUS,
            '-': TokenType.MINUS,
            '*': TokenType.STAR,
            '/': TokenType.SLASH,
            '%': TokenType.PERCENT,
            '~': TokenType.TILDE,
            '=': TokenType.ASSIGN,
            '<': TokenType.LT,
            '>': TokenType.GT,
            '(': TokenType.LPAREN,
            ')': TokenType.RPAREN,
            '[': TokenType.LBRACKET,
            ']': TokenType.RBRACKET,
            '{': TokenType.LBRACE,
            '}': TokenType.RBRACE,
            ',': TokenType.COMMA,
            '.': TokenType.DOT,
            ':': TokenType.COLON,
            '#': TokenType.HASH,
        }
        
        if ch in single_map:
            self.advance()
            return self.make_token(single_map[ch], ch,
                                   start_line, start_col)
        
        self.error(f"Unexpected character: {ch!r}")
    
    def tokenise(self) -> List[Token]:
        """
        Tokenise the entire source, return list of tokens.
        
        Collapses consecutive NEWLINE tokens to a single NEWLINE
        (blank lines don't produce multiple tokens).
        """
        self.pos = 0
        self.line = 1
        self.col = 1
        self.tokens = []
        self.in_asm_block = False
        self._in_asm_keyword = False
        self._prev_keyword = None
        self._last_sig_type = None
        self._newline_since_sig = True
        self._bracket_asm_depth = 0
        
        while True:
            tok = self._next_token()
            
            # Collapse consecutive newlines
            if (tok.type == TokenType.NEWLINE and
                self.tokens and
                self.tokens[-1].type == TokenType.NEWLINE):
                continue
            
            self.tokens.append(tok)
            
            if tok.type == TokenType.EOF:
                break
        
        return self.tokens
    
    def __iter__(self) -> Iterator[Token]:
        """Iterate tokens lazily."""
        self.pos = 0
        self.line = 1
        self.col = 1
        self.in_asm_block = False
        self._in_asm_keyword = False
        self._prev_keyword = None
        self._last_sig_type = None
        self._newline_since_sig = True
        self._bracket_asm_depth = 0
        
        while True:
            tok = self._next_token()
            yield tok
            if tok.type == TokenType.EOF:
                break


# ============================================================
#  PRETTY PRINTER (for debugging / INSPECT command)
# ============================================================

def dump_tokens(tokens: List[Token], show_newlines: bool = False) -> str:
    """
    Format token list as a readable table.
    Used by INSPECT and the geometric view's text layer.
    """
    lines = []
    lines.append(f"{'TYPE':<20} {'VALUE':<25} {'LOCATION':<10}")
    lines.append("─" * 55)
    
    for tok in tokens:
        if tok.type == TokenType.NEWLINE and not show_newlines:
            continue
        if tok.type == TokenType.EOF:
            lines.append(f"{'EOF':<20} {'<end>':<25} L{tok.line}:{tok.col}")
            break
        
        val = tok.value if len(tok.value) <= 24 else tok.value[:21] + "..."
        lines.append(
            f"{tok.type.name:<20} {val:<25} L{tok.line}:{tok.col}"
        )
    
    return '\n'.join(lines)


# ============================================================
#  SELF-TEST
# ============================================================

if __name__ == "__main__":
    test_source = r"""
// Orb Language — Lexer Test Program (with Board System)

MODULE filter DATAFLOW
PORTS
    IN  raw_signal AS VEC[8]
    IN  cutoff     AS FLOAT
    OUT filtered   AS VEC[8]
END PORTS
{
    DIM temp AS VEC = <0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0>
    DIM i AS INT = 0
    
    VLOAD temp FROM raw_signal
    
    REM Apply threshold filter
    FOR i = 0 TO 7
        IF temp[i] > cutoff THEN
            temp[i] = cutoff
        END IF
    NEXT i
    
    VSTORE temp TO filtered
    
    @check_output:
    INSPECT temp
    
    // Drill down to register level
    ASM
        VLOAD R0, R1, R2
        VADD  R0, R1, R3
        CMP   R3, #0
        JEQ   .zero_out:
        MOV   R4, R3
        JMP   .done:
        .zero_out:
        MOV   R4, #0
        .done:
        NOP
    END ASM
}
END MODULE

MODULE logger MESSAGE
{
    DIM msg AS STRING = ""
    RECV msg FROM signal_filter.status TIMEOUT 1000
    PRINT "Received: ", msg
    HALT "Debug checkpoint"
}
END MODULE

// Board-level composition
BOARD audio_processor

    PLACE filter AS low_pass
    PLACE filter AS high_pass AT 200, 100
    PLACE mixer  AS output_mix
    
    WIRE low_pass.filtered  TO output_mix.input_a
    WIRE high_pass.filtered TO output_mix.input_b
    
    SET low_pass.cutoff  = 0.3
    SET high_pass.cutoff = 0.7
    
    ROUTE low_pass.overflow TO logger.alert
    
    SHARE gain_state BETWEEN output_mix, speaker_out
    
    PROBE low_pass.filtered AS "Low Pass Output"
    
    EXPORT output_mix.output AS master_out

END BOARD

// Sub-board used as module
BOARD equaliser
    PLACE filter AS band_1
    PLACE filter AS band_2
    PLACE mixer  AS sum
    
    EXPORT band_1.raw_signal AS input
    EXPORT sum.output        AS output
    
    WIRE band_1.filtered TO sum.input_a
    WIRE band_2.filtered TO sum.input_b
END BOARD

IMPORT "modules/oscillator"

// Shared state module
MODULE counter SHARED
SHARED
    DIM count AS INT = 0
END SHARED
{
    LOCK count
        count = count + 1
        IF count >= 100 THEN
            SEND count TO logger
            count = 0
        END IF
    UNLOCK count
}
END MODULE

// ============================================
// BBC BASIC-style inline assembly tests
// ============================================

MODULE dsp_kernel DATAFLOW
PORTS
    IN  samples AS VEC[8]
    OUT output  AS VEC[8]
END PORTS
{
    DIM gain AS FLOAT = 1.5
    DIM offset AS INT = 4
    DIM result AS INT = 0
    
    // Inline asm with orb42_core_v2 ISA
    [
        ALU D0, D0, D0, XOR      // D0 = 0 (zero register)
        MOV D1, gain              // Variable bridge: reads Orb variable
        ALUI D2, D0, 255          // D2 = 0 + 255
        ALU D1, D1, D2, ADD       // D1 = D1 + D2
        MOV result, D1            // Write back to Orb variable
    ]
    
    PRINT "Result: ", result
    
    // Named ASM block — orb42 peripheral init style
    ASM apply_gain
        LUI D7, 0x0005           // D7 = phaser bank base
        SYS SETDP D7             // DP = 0x00050000
        ALUI D1, D0, 29          // D1 = 29
        STOREDP D1, #0x0C        // PHA_CORE_PINC = 29
        ALUI D1, D0, 1
        STOREDP D1, #0x00        // PHA_ENABLE = 1
        
        FOR pass = 0 TO 2 STEP 2
            OPT pass
            .loop:
            LOADW D2, [A0, 0]    // Load from address in A0
            ALU D2, D2, D3, MUL  // D2 = D2 * D3
            STOREW D2, [A1, 0]   // Store via A1
            ADDQ A0, 4           // A0 += 4
            ADDQ A1, 4           // A1 += 4
            BNE D0, D2, .loop    // Loop until D2 == 0
        NEXT pass
    END ASM
    
    // Coprocessor instructions
    [
        .SCALE EQU 256
        .MASK EQU 0xFF
        
        // Trig via CORDIC
        ALUI D0, D0, 1000       // angle = 1000
        SINCOS                   // D0 = cos, D1 = sin
        
        // Trust verification
        PDRIFT D6                // D6 = composite phaser drift XOR
        PWAVE D5                 // D5 = phaser waveform
        
        // Phaser MAC
        PSEL D0                  // Select phaser core 0
        PCLR                     // Clear MAC accumulator
        PMAC D1, 0x10            // Fire MAC with D1, read result
        
        // Division
        CDIV D3, D4              // D0 = D3/D4, D1 = D3%D4
        RSQRT D2                 // D0 = 1/sqrt(D2)
        
        // Vector unit
        VLOADL D1, 0             // Load D1 into vec A lane 0
        VLOADB D2, 0             // Load D2 into vec B lane 0
        VOP 0                    // Execute vec op 0 (add)
        VREAD D3, 0              // Read result lane 0 into D3
        VDOTRD D4                // Read dot product into D4
    ]
}
END MODULE
"""
    
    print("=" * 60)
    print("  ORB LANGUAGE LEXER — TEST RUN")
    print("=" * 60)
    print()
    
    lexer = Lexer(test_source, "test.orb")
    
    try:
        tokens = lexer.tokenise()
        print(dump_tokens(tokens, show_newlines=False))
        print()
        print(f"Total tokens: {len(tokens)} "
              f"(including {sum(1 for t in tokens if t.type == TokenType.NEWLINE)} newlines)")
        
        # Count by category
        categories = {
            "Keywords":    sum(1 for t in tokens if t.type.value >= TokenType.MODULE.value 
                              and t.type.value <= TokenType.TIMEOUT.value),
            "Identifiers": sum(1 for t in tokens if t.type == TokenType.IDENTIFIER),
            "Literals":    sum(1 for t in tokens if t.type in (
                TokenType.INTEGER_LIT, TokenType.FLOAT_LIT,
                TokenType.HEX_LIT, TokenType.STRING_LIT)),
            "Operators":   sum(1 for t in tokens if t.type.value >= TokenType.PLUS.value
                              and t.type.value <= TokenType.GTE.value),
            "Labels":      sum(1 for t in tokens if t.type in (
                TokenType.LABEL, TokenType.ASM_LABEL)),
            "Registers":   sum(1 for t in tokens if t.type == TokenType.REGISTER),
            "Immediates":  sum(1 for t in tokens if t.type == TokenType.IMMEDIATE),
            "Comments":    sum(1 for t in tokens if t.type == TokenType.COMMENT),
        }
        
        print("\nToken breakdown:")
        for cat, count in categories.items():
            print(f"  {cat:<14} {count}")
            
    except LexerError as e:
        print(f"ERROR: {e}")
