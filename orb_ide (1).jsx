const { useState, useRef, useCallback, useEffect, useMemo, useReducer } = React;

// ============================================================
//  EMBEDDED ORB INTERPRETER (JavaScript port)
// ============================================================

class OrbVec {
  constructor(data = [], width = 0) {
    this.data = data;
    this.width = width || data.length;
  }
  copy() { return new OrbVec([...this.data], this.width); }
  toString() { return `<${this.data.map(v => Number.isInteger(v) ? v : v.toFixed(2)).join(", ")}>`; }
}

function makeDefault(typeName, vecWidth) {
  if (typeName === "INT") return 0;
  if (typeName === "FLOAT") return 0.0;
  if (typeName === "STRING") return "";
  if (typeName === "VEC") return new OrbVec(new Array(vecWidth || 4).fill(0.0), vecWidth || 4);
  return 0;
}

class OrbEnv {
  constructor(parent = null, name = "<global>") {
    this.parent = parent;
    this.name = name;
    this.vars = {};
    this.consts = {};
    this.labels = {};
  }
  get(name) {
    if (name in this.vars) return this.vars[name];
    if (name in this.consts) return this.consts[name];
    if (this.parent) return this.parent.get(name);
    throw new Error(`Undefined: ${name}`);
  }
  set(name, val) {
    let env = this;
    while (env) {
      if (name in env.vars) { env.vars[name] = val; return; }
      env = env.parent;
    }
    this.vars[name] = val;
  }
  define(name, val) { this.vars[name] = val; }
  defineConst(name, val) { this.consts[name] = val; }
  dump() {
    const r = this.parent ? this.parent.dump() : {};
    Object.assign(r, this.vars);
    return r;
  }
}

class OrbRegisters {
  constructor() {
    this.regs = new Array(32).fill(0);
    this.flags = { Z: false, N: false, C: false };
    this.stack = [];
  }
  get(name) { return this.regs[parseInt(name.slice(1))]; }
  set(name, val) { this.regs[parseInt(name.slice(1))] = val; }
  updateFlags(r) {
    if (typeof r === "number") { this.flags.Z = r === 0; this.flags.N = r < 0; }
  }
  dump() {
    const r = {};
    this.regs.forEach((v, i) => { if (v !== 0) r[`R${i}`] = v; });
    r.FLAGS = { ...this.flags };
    return r;
  }
}

// Minimal recursive descent parser for Orb (JS port)
function orbParse(src) {
  // Simplified tokenizer
  const tokens = [];
  let i = 0, line = 1, col = 1;
  const kws = new Set([
    "MODULE","END","PORTS","IN","OUT","INOUT","DIM","CONST","AS","INT","FLOAT","STRING","VEC",
    "IF","THEN","ELIF","ELSE","FOR","TO","STEP","NEXT","WHILE","WEND","GOTO","GOSUB","RETURN",
    "PRINT","INPUT","HALT","INSPECT","VADD","VSUB","VMUL","VDIV","VMADD","VLOAD","VSTORE",
    "VSUM","VDOT","VMIN","VMAX","INTO","FROM","ASM","SEND","RECV","READPORT","LOCK","UNLOCK",
    "TIMEOUT","BOARD","PLACE","ROUTE","SHARE","BETWEEN","SET","PROBE","EXPORT","IMPORT",
    "WIRE","MAIN","AT","DATAFLOW","MESSAGE","SHARED","AND","OR","NOT","OPT","EQU",
    "MOV","ADD","SUB","MUL","DIV","XOR","SHL","SHR","CMP","JMP","JEQ","JNE","JGT","JLT",
    "CALL","RET","PUSH","POP","NOP","HLT","LDA","STA","LDX","STX","JSR","RTS",
    "BEQ","BNE","BCS","BCC","BPL","BMI","SEI","CLI","PHR","PLR"
  ]);
  const asmOps = new Set([
    "MOV","ADD","SUB","MUL","DIV","AND","OR","XOR","NOT","SHL","SHR",
    "VADD","VSUB","VMUL","VDIV","VMADD","VLOAD","VSTORE",
    "CMP","JMP","JEQ","JNE","JGT","JLT","CALL","RET","PUSH","POP","NOP","HLT",
    "LDA","STA","LDX","STX","JSR","RTS","BEQ","BNE","BCS","BCC","BPL","BMI",
    "SEI","CLI","PHR","PLR"
  ]);
  let inAsm = false;

  while (i < src.length) {
    const ch = src[i];
    if (ch === " " || ch === "\t" || ch === "\r") { i++; col++; continue; }
    if (ch === "\n") { tokens.push({ t: "NL", v: "\\n", l: line, c: col }); i++; line++; col = 1; continue; }
    if (ch === "/" && src[i + 1] === "/") {
      while (i < src.length && src[i] !== "\n") i++;
      continue;
    }
    if (ch === "R" && src[i+1] === "E" && src[i+2] === "M" && (i+3>=src.length || !/\w/.test(src[i+3]))) {
      while (i < src.length && src[i] !== "\n") i++;
      continue;
    }
    if (ch === '"') {
      let s = ""; i++; col++;
      while (i < src.length && src[i] !== '"') { s += src[i]; i++; col++; }
      i++; col++;
      tokens.push({ t: "STR", v: s, l: line, c: col });
      continue;
    }
    if (/\d/.test(ch)) {
      let n = "";
      if (ch === "0" && (src[i+1] === "x" || src[i+1] === "X")) {
        n = "0x"; i += 2; col += 2;
        while (i < src.length && /[0-9a-fA-F]/.test(src[i])) { n += src[i]; i++; col++; }
        tokens.push({ t: "HEX", v: n, l: line, c: col }); continue;
      }
      while (i < src.length && /\d/.test(src[i])) { n += src[i]; i++; col++; }
      if (i < src.length && src[i] === "." && /\d/.test(src[i+1])) {
        n += "."; i++; col++;
        while (i < src.length && /\d/.test(src[i])) { n += src[i]; i++; col++; }
        tokens.push({ t: "FLT", v: n, l: line, c: col });
      } else {
        tokens.push({ t: "NUM", v: n, l: line, c: col });
      }
      continue;
    }
    if (/[a-zA-Z_]/.test(ch)) {
      let w = "";
      while (i < src.length && /[\w]/.test(src[i])) { w += src[i]; i++; col++; }
      const up = w.toUpperCase();
      // Compound: END MODULE, END IF, etc.
      if (up === "END") {
        let j = i; while (j < src.length && (src[j] === " " || src[j] === "\t")) j++;
        let w2 = "";
        while (j < src.length && /[\w]/.test(src[j])) { w2 += src[j]; j++; }
        const compound = `END ${w2.toUpperCase()}`;
        if (["END MODULE","END PORTS","END SHARED","END IF","END ASM","END BOARD"].includes(compound)) {
          tokens.push({ t: "KW", v: compound, l: line, c: col });
          i = j; col += (j - i);
          if (compound === "END ASM") inAsm = false;
          continue;
        }
      }
      if (up === "ASM") inAsm = true;
      // Register check
      if (/^R\d+$/.test(up) && parseInt(up.slice(1)) <= 31) {
        tokens.push({ t: "REG", v: up, l: line, c: col }); continue;
      }
      if (kws.has(up)) {
        tokens.push({ t: "KW", v: up, l: line, c: col });
      } else {
        tokens.push({ t: "ID", v: w, l: line, c: col });
      }
      continue;
    }
    if (ch === "@") {
      i++; col++; let w = "";
      while (i < src.length && /[\w]/.test(src[i])) { w += src[i]; i++; col++; }
      tokens.push({ t: "LABEL", v: "@" + w, l: line, c: col }); continue;
    }
    if (ch === "#") {
      if (inAsm || (i + 1 < src.length && (src[i+1] === "(" || /[\d\-]/.test(src[i+1])))) {
        if (src[i+1] === "(") {
          tokens.push({ t: "OP", v: "#", l: line, c: col }); i++; col++; continue;
        }
        let imm = "#"; i++; col++;
        if (src[i] === "-") { imm += "-"; i++; col++; }
        if (src[i] === "0" && (src[i+1] === "x" || src[i+1] === "X")) {
          imm += "0x"; i += 2; col += 2;
          while (i < src.length && /[0-9a-fA-F]/.test(src[i])) { imm += src[i]; i++; col++; }
        } else {
          while (i < src.length && /[\d.]/.test(src[i])) { imm += src[i]; i++; col++; }
        }
        tokens.push({ t: "IMM", v: imm, l: line, c: col }); continue;
      }
    }
    if (ch === "." && i + 1 < src.length && /[a-zA-Z]/.test(src[i+1])) {
      if (inAsm) {
        let lb = "."; i++; col++;
        while (i < src.length && /[\w]/.test(src[i])) { lb += src[i]; i++; col++; }
        if (i < src.length && src[i] === ":") { lb += ":"; i++; col++; }
        tokens.push({ t: "ASMLBL", v: lb, l: line, c: col }); continue;
      }
    }
    // Two-char ops
    if (ch === "=" && src[i+1] === "=") { tokens.push({t:"OP",v:"==",l:line,c:col}); i+=2; col+=2; continue; }
    if (ch === "!" && src[i+1] === "=") { tokens.push({t:"OP",v:"!=",l:line,c:col}); i+=2; col+=2; continue; }
    if (ch === "<" && src[i+1] === "=") { tokens.push({t:"OP",v:"<=",l:line,c:col}); i+=2; col+=2; continue; }
    if (ch === ">" && src[i+1] === "=") { tokens.push({t:"OP",v:">=",l:line,c:col}); i+=2; col+=2; continue; }
    tokens.push({ t: "OP", v: ch, l: line, c: col }); i++; col++;
  }
  tokens.push({ t: "EOF", v: "", l: line, c: col });

  // --- Simplified AST builder ---
  let pos = 0;
  const cur = () => tokens[pos] || { t: "EOF", v: "" };
  const adv = () => tokens[pos++] || { t: "EOF", v: "" };
  const at = (...vs) => vs.some(v => cur().v === v || cur().t === v);
  const expect = (v) => { if (cur().v !== v && cur().t !== v) throw new Error(`Expected ${v}, got ${cur().v} at L${cur().l}`); return adv(); };
  const skipNL = () => { while (cur().t === "NL") adv(); };
  const readId = () => { const t = adv(); return t.v; };

  function parseExpr() { return parseOr(); }
  function parseOr() { let l = parseAnd(); while (cur().v === "OR") { adv(); l = { t: "BinOp", op: "OR", l, r: parseAnd() }; } return l; }
  function parseAnd() { let l = parseNot(); while (cur().v === "AND") { adv(); l = { t: "BinOp", op: "AND", l, r: parseNot() }; } return l; }
  function parseNot() { if (cur().v === "NOT") { adv(); return { t: "UnOp", op: "NOT", a: parseCmp() }; } return parseCmp(); }
  function parseCmp() {
    let l = parseAdd();
    while (["==","!=","<",">","<=",">="].includes(cur().v)) { const op = adv().v; l = { t: "BinOp", op, l, r: parseAdd() }; }
    return l;
  }
  function parseAdd() {
    let l = parseMul();
    while (cur().v === "+" || cur().v === "-") { const op = adv().v; l = { t: "BinOp", op, l, r: parseMul() }; }
    return l;
  }
  function parseMul() {
    let l = parseUnary();
    while (cur().v === "*" || cur().v === "/" || cur().v === "%") { const op = adv().v; l = { t: "BinOp", op, l, r: parseUnary() }; }
    return l;
  }
  function parseUnary() {
    if (cur().v === "-") { adv(); return { t: "UnOp", op: "-", a: parsePostfix() }; }
    if (cur().v === "~") { adv(); return { t: "UnOp", op: "~", a: parsePostfix() }; }
    return parsePostfix();
  }
  function parsePostfix() {
    let e = parsePrimary();
    while (true) {
      if (cur().v === "[" && e.t === "Id") {
        adv(); const idx = parseExpr(); expect("]"); e = { t: "Index", target: e, index: idx };
      } else if (cur().v === ".") {
        adv(); const f = readId(); e = { t: "Dot", target: e, field: f };
      } else if (cur().v === "(" && e.t === "Id") {
        adv(); const args = [];
        if (cur().v !== ")") { args.push(parseExpr()); while (cur().v === ",") { adv(); args.push(parseExpr()); } }
        expect(")"); e = { t: "Call", name: e.name, args };
      } else break;
    }
    return e;
  }
  function parsePrimary() {
    const tk = cur();
    if (tk.t === "NUM") { adv(); return { t: "Num", v: parseInt(tk.v) }; }
    if (tk.t === "FLT") { adv(); return { t: "Num", v: parseFloat(tk.v) }; }
    if (tk.t === "HEX") { adv(); return { t: "Num", v: parseInt(tk.v, 16) }; }
    if (tk.t === "STR") { adv(); return { t: "Str", v: tk.v }; }
    if (tk.t === "ID") { adv(); return { t: "Id", name: tk.v }; }
    if (tk.t === "REG") { adv(); return { t: "Id", name: tk.v }; }
    if (tk.t === "ASMLBL") { adv(); return { t: "Id", name: tk.v.replace(/:$/, "") }; }
    if (tk.t === "IMM") { adv(); const raw = tk.v.slice(1); return { t: "Num", v: raw.startsWith("0x") ? parseInt(raw,16) : parseFloat(raw) }; }
    if (tk.v === "(") { adv(); const e = parseExpr(); expect(")"); return e; }
    if (tk.v === "<") {
      adv(); const els = [parseAdd()]; while (cur().v === ",") { adv(); els.push(parseAdd()); } expect(">");
      return { t: "Vec", elements: els };
    }
    if (tk.v === "#") { adv(); expect("("); const e = parseExpr(); expect(")"); return e; }
    throw new Error(`Unexpected: ${tk.v} (${tk.t}) at L${tk.l}`);
  }

  function parseType() {
    const tn = adv().v;
    let w = null;
    if (cur().v === "[") { adv(); w = parseInt(adv().v); expect("]"); }
    return { typeName: tn, vecWidth: w };
  }

  function parseStmt() {
    skipNL();
    const tk = cur();
    if (tk.t === "EOF") return null;
    if (tk.v === "DIM") {
      adv(); const name = readId(); expect("AS"); const { typeName, vecWidth } = parseType();
      let init = null; if (cur().v === "=") { adv(); init = parseExpr(); }
      return { t: "VarDecl", name, typeName, vecWidth, init };
    }
    if (tk.v === "CONST") {
      adv(); const name = readId(); expect("AS"); const { typeName } = parseType();
      expect("="); const val = parseExpr();
      return { t: "ConstDecl", name, typeName, value: val };
    }
    if (tk.v === "IF") {
      adv(); const cond = parseExpr(); expect("THEN"); skipNL();
      const thenB = []; while (!at("ELIF","ELSE","END IF")) { thenB.push(parseStmt()); skipNL(); }
      const elifs = [];
      while (cur().v === "ELIF") { adv(); const ec = parseExpr(); expect("THEN"); skipNL(); const eb = []; while (!at("ELIF","ELSE","END IF")) { eb.push(parseStmt()); skipNL(); } elifs.push({ cond: ec, body: eb }); }
      let elseB = [];
      if (cur().v === "ELSE") { adv(); skipNL(); while (cur().v !== "END IF") { elseB.push(parseStmt()); skipNL(); } }
      expect("END IF");
      return { t: "IfStmt", cond, thenBody: thenB, elifs, elseBody: elseB };
    }
    if (tk.v === "FOR") {
      adv(); const vn = readId(); expect("="); const start = parseExpr(); expect("TO"); const end = parseExpr();
      let step = null; if (cur().v === "STEP") { adv(); step = parseExpr(); }
      skipNL(); const body = []; while (cur().v !== "NEXT") { body.push(parseStmt()); skipNL(); }
      adv(); if (cur().t === "ID") adv();
      return { t: "ForStmt", varName: vn, start, end, step, body };
    }
    if (tk.v === "WHILE") {
      adv(); const cond = parseExpr(); skipNL();
      const body = []; while (cur().v !== "WEND") { body.push(parseStmt()); skipNL(); }
      adv();
      return { t: "WhileStmt", cond, body };
    }
    if (tk.v === "PRINT") {
      adv(); const vals = [parseExpr()]; while (cur().v === ",") { adv(); vals.push(parseExpr()); }
      return { t: "PrintStmt", values: vals };
    }
    if (tk.v === "HALT") {
      adv(); let msg = ""; if (cur().t === "STR") msg = adv().v;
      return { t: "HaltStmt", message: msg };
    }
    if (tk.v === "INSPECT") { adv(); const tgt = readId(); return { t: "InspectStmt", target: tgt }; }
    if (tk.v === "VLOAD") { adv(); const tgt = readId(); expect("FROM"); const src = parseExpr(); return { t: "VecLoad", target: tgt, source: src }; }
    if (tk.v === "VSTORE") { adv(); const src = readId(); expect("TO"); const tgt = parseExpr(); return { t: "VecStore", source: src, target: tgt }; }
    if (["VADD","VSUB","VMUL","VDIV","VMADD"].includes(tk.v)) {
      const op = adv().v; const a = parseExpr(); expect(","); const b = parseExpr(); expect("INTO"); const tgt = readId();
      return { t: "VecArith", op, a, b, target: tgt };
    }
    if (["VSUM","VDOT","VMIN","VMAX"].includes(tk.v)) {
      const op = adv().v; const src = parseExpr(); expect("INTO"); const tgt = readId();
      return { t: "VecReduce", op, source: src, target: tgt };
    }
    if (tk.v === "SEND") { adv(); const val = parseExpr(); expect("TO"); const tgt = readId(); let ch=null; if(cur().v==="."){adv();ch=readId();} return { t:"SendStmt", value:val, targetMod:tgt, targetCh:ch }; }
    if (tk.v === "RECV") { adv(); const v=readId(); expect("FROM"); const sm=readId(); let sc=null; if(cur().v==="."){adv();sc=readId();} let to=null; if(cur().v==="TIMEOUT"){adv();to=parseExpr();} return { t:"RecvStmt", variable:v, srcMod:sm, srcCh:sc, timeout:to }; }
    if (tk.v === "LOCK") { adv(); const tgt=readId(); skipNL(); const body=[]; while(cur().v!=="UNLOCK"){body.push(parseStmt());skipNL();} adv(); readId(); return { t:"LockStmt", target:tgt, body }; }
    if (tk.v === "RETURN") { adv(); let val=null; if(cur().t!=="NL"&&cur().t!=="EOF") val=parseExpr(); return { t:"ReturnStmt", value:val }; }
    if (tk.v === "GOTO") { adv(); return { t: "GotoStmt", target: readId() }; }
    if (tk.v === "GOSUB") { adv(); return { t: "GosubStmt", target: readId() }; }
    if (tk.t === "LABEL") { const name = adv().v.slice(1); if (cur().v === ":") adv(); return { t: "LabelStmt", name }; }
    // ASM block
    if (tk.v === "ASM") {
      adv(); let name = null; if (cur().t === "ID") name = readId();
      skipNL(); const body = [];
      while (cur().v !== "END ASM") { body.push(parseAsmLine()); skipNL(); }
      adv();
      return { t: "AsmBlock", name, body };
    }
    // Inline asm [...]
    if (tk.v === "[") {
      adv(); skipNL(); const body = [];
      while (cur().v !== "]") { body.push(parseAsmLine()); skipNL(); }
      adv();
      return { t: "AsmInline", body };
    }
    // Assignment or expression
    if (tk.t === "ID") {
      const name = tk.v;
      if (tokens[pos + 1]?.v === "=" && tokens[pos + 1]?.v !== "==") {
        adv(); adv(); const val = parseExpr();
        return { t: "Assignment", target: name, value: val };
      }
      if (tokens[pos + 1]?.v === "[") {
        adv(); adv(); const idx = parseExpr(); expect("]"); expect("=");
        const val = parseExpr();
        return { t: "Assignment", target: name, index: idx, value: val };
      }
      const expr = parseExpr();
      return { t: "ExprStmt", expr };
    }
    adv(); return null;
  }

  function parseAsmLine() {
    skipNL();
    const tk = cur();
    if (tk.t === "ASMLBL" && tk.v.endsWith(":")) {
      adv(); return { t: "AsmLabelDef", name: tk.v.slice(1, -1) };
    }
    if (tk.t === "ASMLBL" && !tk.v.endsWith(":") && tokens[pos+1]?.v === "EQU") {
      const name = adv().v.slice(1); adv(); const val = parseExpr();
      return { t: "AsmEquate", name, value: val };
    }
    if (tk.v === "OPT") { adv(); const val = parseExpr(); return { t: "AsmOpt", value: val }; }
    if (asmOps.has(tk.v?.toUpperCase())) {
      const op = adv().v; const operands = [];
      if (cur().t !== "NL" && cur().t !== "EOF" && cur().v !== "]" && cur().v !== "END ASM") {
        operands.push(parseAsmOperand());
        while (cur().v === ",") { adv(); operands.push(parseAsmOperand()); }
      }
      return { t: "AsmInstr", op, operands };
    }
    // Orb statement inside asm
    return parseStmt();
  }

  function parseAsmOperand() {
    const tk = cur();
    if (tk.t === "REG") { adv(); return { kind: "reg", v: tk.v }; }
    if (tk.t === "IMM") { adv(); const raw=tk.v.slice(1); return { kind: "imm", v: raw.startsWith("0x")?parseInt(raw,16):parseFloat(raw) }; }
    if (tk.v === "#") { adv(); expect("("); const e = parseExpr(); expect(")"); return { kind: "exprimm", expr: e }; }
    if (tk.v === "[") {
      adv(); const reg = adv().v; let off = null;
      if (cur().v === ",") { adv(); off = parseExpr(); }
      expect("]");
      return off ? { kind: "indexed", v: reg, offset: off } : { kind: "indirect", v: reg };
    }
    if (tk.t === "ASMLBL") { adv(); return { kind: "lblref", v: tk.v.replace(/:$/, "") }; }
    if (tk.t === "ID") { adv(); return { kind: "var", v: tk.v }; }
    const val = parseExpr();
    return { kind: "expr", expr: val };
  }

  function parsePortDecl() {
    const dir = adv().v; const name = readId(); expect("AS"); const { typeName, vecWidth } = parseType();
    return { t: "PortDecl", direction: dir, name, typeName, vecWidth };
  }

  function parseModule() {
    expect("MODULE"); const name = readId();
    let commMode = "DATAFLOW";
    if (at("DATAFLOW","MESSAGE","SHARED")) commMode = adv().v;
    skipNL();
    let ports = [];
    if (cur().v === "PORTS") { adv(); skipNL(); while (cur().v !== "END PORTS") { ports.push(parsePortDecl()); skipNL(); } adv(); skipNL(); }
    let sharedVars = [];
    if (cur().v === "SHARED") { adv(); skipNL(); while (cur().v !== "END SHARED") { sharedVars.push(parseStmt()); skipNL(); } adv(); skipNL(); }
    let body = [];
    if (cur().v === "{") { adv(); skipNL(); while (cur().v !== "}") { const s = parseStmt(); if (s) body.push(s); skipNL(); } adv(); skipNL(); }
    expect("END MODULE");
    return { t: "ModuleDecl", name, commMode, ports, sharedVars, body };
  }

  function parseBoard() {
    expect("BOARD"); const name = readId(); skipNL();
    const body = [];
    while (cur().v !== "END BOARD") {
      skipNL();
      const tk = cur();
      if (tk.v === "PLACE") { adv(); const mt=readId(); expect("AS"); const iname=readId(); let px=null,py=null; if(cur().v==="AT"){adv();px=parseExpr();expect(",");py=parseExpr();} body.push({t:"PlaceStmt",moduleType:mt,instanceName:iname,px,py}); }
      else if (tk.v === "WIRE") { adv(); const sm=readId(); expect("."); const sp=readId(); expect("TO"); const dm=readId(); expect("."); const dp=readId(); body.push({t:"WireStmt",srcMod:sm,srcPort:sp,dstMod:dm,dstPort:dp}); }
      else if (tk.v === "ROUTE") { adv(); const sm=readId(); expect("."); const sp=readId(); expect("TO"); const dm=readId(); expect("."); const dp=readId(); body.push({t:"RouteStmt",srcMod:sm,srcPort:sp,dstMod:dm,dstPort:dp}); }
      else if (tk.v === "SHARE") { adv(); const sn=readId(); expect("BETWEEN"); const mods=[readId()]; while(cur().v===","){adv();mods.push(readId());} body.push({t:"ShareStmt",stateName:sn,modules:mods}); }
      else if (tk.v === "SET") { adv(); const m=readId(); expect("."); const p=readId(); expect("="); const val=parseExpr(); body.push({t:"SetStmt",mod:m,port:p,value:val}); }
      else if (tk.v === "PROBE") { adv(); const m=readId(); expect("."); const p=readId(); let lb=null; if(cur().v==="AS"){adv();lb=adv().v;} body.push({t:"ProbeStmt",mod:m,port:p,label:lb}); }
      else if (tk.v === "EXPORT") { adv(); const m=readId(); expect("."); const p=readId(); expect("AS"); const en=readId(); body.push({t:"ExportStmt",mod:m,port:p,extName:en}); }
      else { adv(); }
      skipNL();
    }
    expect("END BOARD");
    return { t: "BoardDecl", name, body };
  }

  // Parse program
  const program = { t: "Program", body: [] };
  skipNL();
  while (cur().t !== "EOF") {
    skipNL();
    if (cur().t === "EOF") break;
    if (cur().v === "MODULE") program.body.push(parseModule());
    else if (cur().v === "BOARD") program.body.push(parseBoard());
    else if (cur().v === "IMPORT") { adv(); const p = adv().v; program.body.push({ t: "ImportStmt", path: p }); }
    else adv();
    skipNL();
  }
  return program;
}

// Interpreter
class OrbInterpreter {
  constructor(outputFn) {
    this.moduleTypes = {};
    this.instances = {};
    this.wires = [];
    this.routes = [];
    this.sharedStates = {};
    this.globalEnv = new OrbEnv(null, "<global>");
    this.regs = new OrbRegisters();
    this.asmLabels = {};
    this.outputFn = outputFn || ((...a) => console.log(...a));
    this.output = [];
    this.trace = [];
    this.inspectLog = [];
    this.halted = false;
    this.haltMsg = "";
    this.error = null;
  }

  run(src) {
    try {
      const ast = orbParse(src);
      // Phase 1: register types
      for (const n of ast.body) {
        if (n.t === "ModuleDecl") this.moduleTypes[n.name] = n;
      }
      // Phase 2-4: execute boards
      for (const n of ast.body) {
        if (n.t === "BoardDecl") this.execBoard(n);
      }
    } catch (e) {
      this.error = e.message;
      this.outputFn(`ERROR: ${e.message}`);
    }
  }

  execBoard(board) {
    for (const n of board.body) {
      if (n.t === "PlaceStmt") this.placeInstance(n);
    }
    for (const n of board.body) {
      if (n.t === "WireStmt") this.wires.push([n.srcMod, n.srcPort, n.dstMod, n.dstPort]);
      else if (n.t === "RouteStmt") this.routes.push([n.srcMod, n.srcPort, n.dstMod, n.dstPort]);
      else if (n.t === "ShareStmt") this.sharedStates[n.stateName] = { name: n.stateName, value: null, lockedBy: null, modules: n.modules };
      else if (n.t === "SetStmt") this.applySet(n);
    }
    this.executeInstances();
  }

  placeInstance(node) {
    const def = this.moduleTypes[node.moduleType];
    if (!def) throw new Error(`Unknown module: ${node.moduleType}`);
    const env = new OrbEnv(this.globalEnv, node.instanceName);
    const inst = {
      name: node.instanceName, moduleType: node.moduleType,
      commMode: def.commMode, def, env,
      inPorts: {}, outPorts: {}, msgChannels: {},
      executed: false, halted: false, haltMsg: ""
    };
    for (const p of def.ports) {
      const port = { name: p.name, dir: p.direction, typeName: p.typeName, vecWidth: p.vecWidth, value: makeDefault(p.typeName, p.vecWidth), connected: false };
      if (p.direction === "IN" || p.direction === "INOUT") { inst.inPorts[p.name] = port; env.define(p.name, port.value); }
      if (p.direction === "OUT" || p.direction === "INOUT") { inst.outPorts[p.name] = port; if (p.direction === "OUT") env.define(p.name, port.value); }
    }
    this.instances[node.instanceName] = inst;
  }

  applySet(node) {
    const inst = this.instances[node.mod];
    if (!inst) return;
    const val = this.evalExpr(node.value, this.globalEnv);
    if (node.port in inst.inPorts) { inst.inPorts[node.port].value = val; inst.env.set(node.port, val); }
    else inst.env.define(node.port, val);
  }

  executeInstances() {
    const deps = {}; for (const n in this.instances) deps[n] = new Set();
    for (const [sm,,dm] of this.wires) { if (deps[dm]) deps[dm].add(sm); }
    const visited = new Set(), order = [];
    const visit = (n) => { if (visited.has(n)) return; visited.add(n); (deps[n]||new Set()).forEach(d => visit(d)); order.push(n); };
    for (const n in this.instances) visit(n);

    for (const name of order) {
      const inst = this.instances[name];
      // Sync input ports
      for (const [sm, sp, dm, dp] of this.wires) {
        if (dm === name) {
          const si = this.instances[sm];
          if (si && sp in si.outPorts) {
            const val = si.outPorts[sp].value;
            if (dp in inst.inPorts) { inst.inPorts[dp].value = val; inst.env.set(dp, val instanceof OrbVec ? val.copy() : val); }
          }
        }
      }
      try {
        this.execBody(inst.def.body, inst.env);
        inst.executed = true;
        for (const pn in inst.outPorts) { try { inst.outPorts[pn].value = inst.env.get(pn); } catch(e) {} }
      } catch (e) {
        if (e.message?.startsWith("HALT:")) { inst.halted = true; inst.haltMsg = e.message.slice(5); this.halted = true; this.haltMsg = `[${name}] ${inst.haltMsg}`; }
        else { this.error = `[${name}] ${e.message}`; this.outputFn(`ERROR in ${name}: ${e.message}`); }
      }
    }
  }

  execBody(body, env) {
    if (!body) return;
    for (let i = 0; i < body.length; i++) {
      if (body[i]?.t === "LabelStmt") env.labels[body[i].name] = i;
    }
    let i = 0;
    while (i < body.length) {
      const node = body[i];
      if (!node) { i++; continue; }
      this.trace.push({ type: node.t, line: node.l });
      const result = this.execStmt(node, env);
      if (typeof result === "string" && result.startsWith("__goto:")) {
        const target = result.slice(7);
        if (target in env.labels) { i = env.labels[target]; continue; }
      }
      i++;
    }
  }

  execStmt(node, env) {
    if (!node) return;
    switch (node.t) {
      case "VarDecl": {
        let val = makeDefault(node.typeName, node.vecWidth);
        if (node.init) val = this.evalExpr(node.init, env);
        env.define(node.name, val); break;
      }
      case "ConstDecl": env.defineConst(node.name, this.evalExpr(node.value, env)); break;
      case "Assignment": {
        const val = this.evalExpr(node.value, env);
        if (node.index) {
          const idx = this.evalExpr(node.index, env);
          const tgt = env.get(node.target);
          if (tgt instanceof OrbVec) tgt.data[Math.floor(idx)] = Number(val);
        } else env.set(node.target, val);
        break;
      }
      case "IfStmt": {
        if (this.truthy(this.evalExpr(node.cond, env))) { this.execBody(node.thenBody, env); }
        else {
          let matched = false;
          for (const el of (node.elifs||[])) { if (this.truthy(this.evalExpr(el.cond, env))) { this.execBody(el.body, env); matched = true; break; } }
          if (!matched && node.elseBody?.length) this.execBody(node.elseBody, env);
        }
        break;
      }
      case "ForStmt": {
        let c = this.evalExpr(node.start, env);
        const end = this.evalExpr(node.end, env);
        const step = node.step ? this.evalExpr(node.step, env) : 1;
        env.define(node.varName, c);
        while ((step > 0 && c <= end) || (step < 0 && c >= end)) {
          env.set(node.varName, c); this.execBody(node.body, env); c += step;
        }
        break;
      }
      case "WhileStmt": { while (this.truthy(this.evalExpr(node.cond, env))) this.execBody(node.body, env); break; }
      case "PrintStmt": {
        const parts = node.values.map(v => { const r = this.evalExpr(v, env); return r instanceof OrbVec ? r.toString() : String(r); });
        const line = parts.join("");
        this.output.push(line); this.outputFn(line); break;
      }
      case "HaltStmt": throw new Error(`HALT:${node.message}`);
      case "InspectStmt": {
        const val = env.get(node.target);
        this.inspectLog.push({ target: node.target, value: val instanceof OrbVec ? val.toString() : String(val) });
        this.outputFn(`[INSPECT ${node.target}] = ${val instanceof OrbVec ? val.toString() : val}`);
        break;
      }
      case "LabelStmt": break;
      case "GotoStmt": return `__goto:${node.target}`;
      case "VecLoad": { const src = this.evalExpr(node.source, env); env.set(node.target, src instanceof OrbVec ? src.copy() : src); break; }
      case "VecStore": { const src = env.get(node.source); if (node.target?.t === "Id") env.set(node.target.name, src instanceof OrbVec ? src.copy() : src); break; }
      case "VecArith": {
        const a = this.evalExpr(node.a, env), b = this.evalExpr(node.b, env);
        if (a instanceof OrbVec && b instanceof OrbVec) {
          const w = Math.min(a.width, b.width), r = new OrbVec(new Array(w).fill(0), w);
          const ops = { VADD:(x,y)=>x+y, VSUB:(x,y)=>x-y, VMUL:(x,y)=>x*y, VDIV:(x,y)=>y?x/y:0, VMADD:(x,y)=>x*y };
          for (let i=0;i<w;i++) r.data[i] = (ops[node.op]||ops.VADD)(a.data[i], b.data[i]);
          env.set(node.target, r);
        }
        break;
      }
      case "SendStmt": break; // Message passing conceptual in single-threaded
      case "RecvStmt": env.set(node.variable, ""); break;
      case "LockStmt": this.execBody(node.body, env); break;
      case "AsmInline": case "AsmBlock": this.execAsmBody(node.body, env); break;
      case "AsmInstr": this.execAsmInstr(node, env); break;
      case "AsmLabelDef": break;
      case "AsmEquate": { const v = this.evalExpr(node.value, env); env.defineConst(`.${node.name}`, v); this.asmLabels[`.${node.name}`] = v; break; }
      case "AsmOpt": break;
    }
    return null;
  }

  execAsmBody(body, env) {
    if (!body) return;
    for (let i = 0; i < body.length; i++) {
      const n = body[i]; if (!n) continue;
      if (n.t === "AsmLabelDef") this.asmLabels[`.${n.name}`] = i;
      if (n.t === "AsmEquate") { const v = this.evalExpr(n.value, env); env.defineConst(`.${n.name}`, v); this.asmLabels[`.${n.name}`] = v; }
    }
    let i = 0;
    while (i < body.length) {
      const n = body[i]; if (!n) { i++; continue; }
      if (n.t === "AsmInstr") {
        const r = this.execAsmInstr(n, env);
        if (typeof r === "string" && r.startsWith("__jmp:")) {
          const tgt = r.slice(6);
          if (tgt in this.asmLabels && typeof this.asmLabels[tgt] === "number") { i = this.asmLabels[tgt]; continue; }
        }
      } else if (n.t !== "AsmLabelDef" && n.t !== "AsmEquate" && n.t !== "AsmOpt") {
        this.execStmt(n, env);
      }
      i++;
    }
  }

  execAsmInstr(node, env) {
    const op = node.op.toUpperCase(), ops = node.operands || [];
    const resolve = (o) => {
      if (!o) return 0;
      if (o.kind === "reg") return this.regs.get(o.v);
      if (o.kind === "imm") return o.v;
      if (o.kind === "exprimm") return this.evalExpr(o.expr, env);
      if (o.kind === "var") return env.get(o.v);
      if (o.kind === "indirect") return this.regs.get(o.v);
      if (o.kind === "indexed") return this.regs.get(o.v) + this.evalExpr(o.offset, env);
      if (o.kind === "lblref") return o.v;
      if (o.kind === "expr") return this.evalExpr(o.expr, env);
      return 0;
    };
    const store = (o, v) => {
      if (o.kind === "reg") this.regs.set(o.v, v);
      else if (o.kind === "var") env.set(o.v, v);
      else if (o.kind === "indirect" || o.kind === "indexed") this.regs.set(o.v, v);
    };
    const n = ops.length;
    if (["MOV","LDA","LDX"].includes(op) && n >= 2) { store(ops[0], resolve(ops[1])); }
    else if (["STA","STX"].includes(op) && n >= 2) { store(ops[1], resolve(ops[0])); }
    else if (op==="ADD") { if(n===3){const r=resolve(ops[1])+resolve(ops[2]);store(ops[0],r);this.regs.updateFlags(r);}else if(n===2){const r=resolve(ops[0])+resolve(ops[1]);store(ops[0],r);this.regs.updateFlags(r);} }
    else if (op==="SUB") { if(n===3){const r=resolve(ops[1])-resolve(ops[2]);store(ops[0],r);this.regs.updateFlags(r);}else if(n===2){const r=resolve(ops[0])-resolve(ops[1]);store(ops[0],r);this.regs.updateFlags(r);} }
    else if (op==="MUL") { if(n===3){const r=resolve(ops[1])*resolve(ops[2]);store(ops[0],r);this.regs.updateFlags(r);}else if(n===2){const r=resolve(ops[0])*resolve(ops[1]);store(ops[0],r);this.regs.updateFlags(r);} }
    else if (op==="CMP" && n>=2) { this.regs.updateFlags(resolve(ops[0])-resolve(ops[1])); }
    else if (op==="JMP"&&n>=1) return `__jmp:${resolve(ops[0])}`;
    else if ((op==="JEQ"||op==="BEQ")&&n>=1&&this.regs.flags.Z) return `__jmp:${resolve(ops[0])}`;
    else if ((op==="JNE"||op==="BNE")&&n>=1&&!this.regs.flags.Z) return `__jmp:${resolve(ops[0])}`;
    else if (op==="PUSH"||op==="PHR") { if(n>=1) this.regs.stack.push(resolve(ops[0])); }
    else if (op==="POP"||op==="PLR") { if(n>=1&&this.regs.stack.length) store(ops[0], this.regs.stack.pop()); }
    else if (["AND","OR","XOR","NOT","SHL","SHR","SEI","CLI","NOP","HLT","JSR","RTS","RET"].includes(op)) { /* minimal */ }
    return null;
  }

  evalExpr(node, env) {
    if (!node) return 0;
    if (typeof node === "number") return node;
    if (typeof node === "string") return node;
    switch (node.t) {
      case "Num": return node.v;
      case "Str": return node.v;
      case "Id":
        if (node.name in this.asmLabels) return this.asmLabels[node.name];
        return env.get(node.name);
      case "Vec": return new OrbVec(node.elements.map(e => Number(this.evalExpr(e, env))), node.elements.length);
      case "BinOp": {
        const l = this.evalExpr(node.l, env), r = this.evalExpr(node.r, env);
        if (typeof l==="string"||typeof r==="string") { if(node.op==="+") return String(l)+String(r); return ({"==":l==r,"!=":l!=r})[node.op]?1:0; }
        const ops={"+":()=>l+r,"-":()=>l-r,"*":()=>l*r,"/":()=>r?l/r:0,"%":()=>r?l%r:0,"==":()=>l===r?1:0,"!=":()=>l!==r?1:0,"<":()=>l<r?1:0,">":()=>l>r?1:0,"<=":()=>l<=r?1:0,">=":()=>l>=r?1:0,"AND":()=>l&&r?1:0,"OR":()=>l||r?1:0};
        return (ops[node.op]||(() => 0))();
      }
      case "UnOp":
        if (node.op==="-") return -this.evalExpr(node.a, env);
        if (node.op==="~") return ~Math.floor(this.evalExpr(node.a, env));
        if (node.op==="NOT") return this.truthy(this.evalExpr(node.a, env))?0:1;
        return 0;
      case "Index": {
        const tgt = this.evalExpr(node.target, env), idx = this.evalExpr(node.index, env);
        if (tgt instanceof OrbVec) return tgt.data[Math.floor(idx)];
        return 0;
      }
      case "Dot": return 0;
      case "Call": {
        const args = node.args.map(a => this.evalExpr(a, env));
        const builtins = { abs:a=>Math.abs(a[0]), sqrt:a=>Math.sqrt(a[0]), sin:a=>Math.sin(a[0]), cos:a=>Math.cos(a[0]), floor:a=>Math.floor(a[0]), ceil:a=>Math.ceil(a[0]), min:a=>Math.min(...a), max:a=>Math.max(...a), int:a=>Math.floor(a[0]), float:a=>Number(a[0]), str:a=>String(a[0]) };
        const fn = builtins[node.name?.toLowerCase()];
        return fn ? fn(args) : 0;
      }
    }
    return 0;
  }
  truthy(v) { if (typeof v==="number") return v!==0; if (typeof v==="string") return v.length>0; if (v instanceof OrbVec) return v.data.some(x=>x!==0); return Boolean(v); }

  getState() {
    const insts = {};
    for (const [name, inst] of Object.entries(this.instances)) {
      insts[name] = {
        moduleType: inst.moduleType, commMode: inst.commMode,
        executed: inst.executed, halted: inst.halted,
        inPorts: Object.fromEntries(Object.entries(inst.inPorts).map(([k,v])=>[k,{value: v.value instanceof OrbVec ? v.value.toString() : v.value, type: v.typeName}])),
        outPorts: Object.fromEntries(Object.entries(inst.outPorts).map(([k,v])=>[k,{value: v.value instanceof OrbVec ? v.value.toString() : v.value, type: v.typeName}])),
        vars: Object.fromEntries(Object.entries(inst.env.dump()).map(([k,v])=>[k, v instanceof OrbVec ? v.toString() : v])),
      };
    }
    return { instances: insts, registers: this.regs.dump(), shared: this.sharedStates, halted: this.halted, error: this.error, output: [...this.output], inspectLog: [...this.inspectLog] };
  }
}


// ============================================================
//  COLOUR PALETTE
// ============================================================
const C = {
  bg:"#080c14", panel:"#0d1117", panelBorder:"#1b2433", headerBg:"#0f1520",
  modFill:"#111827", modStroke:"#2563eb", modHeader:"#152040", modHeaderText:"#7db4f5",
  modText:"#c8d6e5",
  portIn:"#22d3ee", portOut:"#f59e0b",
  wireData:"#3b82f6", wireMsg:"#f97316",
  flowBox:"#1a2332", flowStroke:"#2d3f56", flowText:"#94a3b8",
  flowDiamond:"#1a2332", flowDiamondStroke:"#eab308", flowDiamondText:"#fde68a",
  flowLoop:"#0c1e30", flowLoopStroke:"#06b6d4", flowLoopText:"#67e8f9",
  flowAsm:"#1a1540", flowAsmStroke:"#6366f1", flowAsmText:"#a5b4fc",
  flowHalt:"#ef4444", flowInspect:"#8b5cf6", flowLabel:"#10b981",
  setValue:"#34d399", probe:"#f472b6",
  sharedFill:"#180a30", sharedStroke:"#a855f7",
  accent:"#60a5fa", dim:"#374151", success:"#22c55e", danger:"#ef4444",
  typeVec:"#3b82f6", typeInt:"#22c55e", typeFloat:"#f59e0b", typeStr:"#e2e8f0",
};
const TC = { VEC:C.typeVec, INT:C.typeInt, FLOAT:C.typeFloat, STRING:C.typeStr };
const COMM = { DATAFLOW:{color:C.wireData,dash:"",label:"DATAFLOW"}, MESSAGE:{color:C.wireMsg,dash:"6,4",label:"MESSAGE"}, SHARED:{color:C.sharedStroke,dash:"3,3",label:"SHARED"} };
const FONT = "'JetBrains Mono','Fira Code','Cascadia Code',monospace";

// ============================================================
//  LAYOUT
// ============================================================
function layoutBoard(board, modTypes, state) {
  const places = board.body.filter(n=>n.t==="PlaceStmt");
  const wires = board.body.filter(n=>n.t==="WireStmt");
  const routes = board.body.filter(n=>n.t==="RouteStmt");
  const sets = board.body.filter(n=>n.t==="SetStmt");
  const probes = board.body.filter(n=>n.t==="ProbeStmt");
  const shares = board.body.filter(n=>n.t==="ShareStmt");
  const exports = board.body.filter(n=>n.t==="ExportStmt");
  const MW=210, PH=28, PAD=70, CW=MW+180;
  // Dependency sort
  const deps={}; places.forEach(p=>{deps[p.instanceName]=new Set();});
  wires.forEach(w=>{if(deps[w.dstMod])deps[w.dstMod].add(w.srcMod);});
  const vis=new Set(),order=[];
  const visit=n=>{if(vis.has(n))return;vis.add(n);(deps[n]||new Set()).forEach(d=>visit(d));order.push(n);};
  places.forEach(p=>visit(p.instanceName));
  const colA={}; order.forEach(n=>{let c=0;(deps[n]||new Set()).forEach(d=>{if(colA[d]!==undefined)c=Math.max(c,colA[d]+1);});colA[n]=c;});
  const colR={}, insts={};
  places.forEach(p => {
    const col=colA[p.instanceName]||0;
    if(!colR[col])colR[col]=0;
    const row=colR[col]++;
    const mod=modTypes[p.moduleType];
    const inP=(mod?.ports||[]).filter(pt=>pt.direction==="IN"||pt.direction==="INOUT");
    const outP=(mod?.ports||[]).filter(pt=>pt.direction==="OUT"||pt.direction==="INOUT");
    const maxP=Math.max(inP.length,outP.length,1);
    const h=60+maxP*PH;
    const x=PAD+col*CW, y=PAD+row*(h+PAD);
    // Get live port values from state
    const instState = state?.instances?.[p.instanceName];
    insts[p.instanceName] = {
      ...p, x,y,w:MW,h, mod, commMode:mod?.commMode||"DATAFLOW",
      inPorts: inP.map((pt,i)=>({...pt,cx:x,cy:y+42+i*PH+PH/2,liveValue:instState?.inPorts?.[pt.name]?.value})),
      outPorts: outP.map((pt,i)=>({...pt,cx:x+MW,cy:y+42+i*PH+PH/2,liveValue:instState?.outPorts?.[pt.name]?.value})),
      executed: instState?.executed, instHalted: instState?.halted,
    };
  });
  const rWires = wires.map(w=>{
    const si=insts[w.srcMod],di=insts[w.dstMod];
    const sp=si?.outPorts?.find(p=>p.name===w.srcPort); const dp=di?.inPorts?.find(p=>p.name===w.dstPort);
    return {...w,sx:sp?.cx||0,sy:sp?.cy||0,dx:dp?.cx||0,dy:dp?.cy||0,style:"data"};
  });
  const rRoutes = routes.map(r=>{
    const si=insts[r.srcMod],di=insts[r.dstMod];
    return {...r,sx:si?(si.x+si.w):0,sy:si?(si.y+si.h/2):0,dx:di?di.x:0,dy:di?(di.y+di.h/2):0,style:"message"};
  });
  let mx=0,my=0; Object.values(insts).forEach(i=>{mx=Math.max(mx,i.x+i.w+PAD);my=Math.max(my,i.y+i.h+PAD);});
  return {insts,wires:rWires,routes:rRoutes,sets,probes,shares,exports,bounds:{w:mx+80,h:my+80}};
}

function layoutFlow(body) {
  const W=230,H=38,G=16,SX=50,AH=20; let y=44;
  const nodes=[];
  function add(n, x=SX) {
    let shape="box",label="",fill=C.flowBox,stroke=C.flowStroke,tc=C.flowText,h=H,ch=null;
    switch(n.t) {
      case "VarDecl": label=`DIM ${n.name} AS ${n.typeName}${n.init?" = ...":""}`;break;
      case "Assignment": label=`${n.target}${n.index?" [...]":""} = ...`;break;
      case "VecLoad": label=`VLOAD ${n.target}`;fill="#0c2d48";stroke=C.typeVec;break;
      case "VecStore": label=`VSTORE ${n.source}`;fill="#0c2d48";stroke=C.typeVec;break;
      case "VecArith": label=`${n.op} → ${n.target}`;fill="#0c2d48";stroke=C.typeVec;break;
      case "PrintStmt": label="PRINT ...";fill="#161f2d";stroke="#4b5563";break;
      case "SendStmt": label=`SEND → ${n.targetMod}`;fill="#2a1a0a";stroke=C.wireMsg;tc="#fdba74";break;
      case "RecvStmt": label=`RECV ← ${n.srcMod}`;fill="#2a1a0a";stroke=C.wireMsg;tc="#fdba74";break;
      case "HaltStmt": shape="octagon";label=`HALT${n.message?": "+n.message:""}`;fill="#2d0a0a";stroke=C.flowHalt;tc="#fca5a5";break;
      case "InspectStmt": shape="hexagon";label=`INSPECT ${n.target}`;fill="#1a0d2e";stroke=C.flowInspect;tc="#c4b5fd";break;
      case "LabelStmt": shape="label";label=`@${n.name}`;fill="transparent";stroke=C.flowLabel;tc=C.flowLabel;h=26;break;
      case "ForStmt": shape="loop";label=`FOR ${n.varName}`;fill=C.flowLoop;stroke=C.flowLoopStroke;tc=C.flowLoopText;ch=n.body;break;
      case "WhileStmt": shape="loop";label="WHILE ...";fill=C.flowLoop;stroke=C.flowLoopStroke;tc=C.flowLoopText;ch=n.body;break;
      case "IfStmt": shape="diamond";label="IF ...";fill=C.flowDiamond;stroke=C.flowDiamondStroke;tc=C.flowDiamondText;ch=n.thenBody;break;
      case "LockStmt": shape="box";label=`LOCK ${n.target}`;fill=C.sharedFill;stroke=C.sharedStroke;tc="#c4b5fd";ch=n.body;break;
      case "AsmInline": shape="asm";label="[ inline asm ]";fill=C.flowAsm;stroke=C.flowAsmStroke;tc=C.flowAsmText;h=H+(n.body?.length||0)*AH+6;break;
      case "AsmBlock": shape="asm";label=`ASM ${n.name||""}`;fill=C.flowAsm;stroke=C.flowAsmStroke;tc=C.flowAsmText;h=H+Math.min(n.body?.length||0,12)*AH+6;break;
      default: label=n.t||"?";
    }
    nodes.push({id:nodes.length,n,x,y,w:W,h,shape,label,fill,stroke,tc});
    y+=h+G;
    if(ch&&shape!=="asm"){ch.forEach(c=>add(c,x+28));}
    if(n.t==="IfStmt") {
      (n.elifs||[]).forEach(el=>{nodes.push({id:nodes.length,n:el,x:x+28,y,w:W,h:H,shape:"diamond",label:`ELIF`,fill:C.flowDiamond,stroke:"#a16207",tc:C.flowDiamondText});y+=H+G;(el.body||[]).forEach(c=>add(c,x+56));});
      if(n.elseBody?.length){nodes.push({id:nodes.length,n:{t:"Else"},x:x+28,y,w:W,h:26,shape:"box",label:"ELSE",fill:C.flowDiamond,stroke:"#a16207",tc:C.flowDiamondText});y+=26+G;n.elseBody.forEach(c=>add(c,x+56));}
    }
  }
  (body||[]).forEach(n=>add(n));
  return {nodes,bounds:{w:420,h:y+30}};
}

// ============================================================
//  SAMPLE SOURCE
// ============================================================
const SAMPLE = `// Orb Language — Live Demo
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

    // BBC BASIC inline asm
    [
        MOV R0, gain
        MOV R1, #10
        ADD R0, R0, R1
        MOV gain, R0
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
    PRINT "Mixed: ", output
}
END MODULE

MODULE logger MESSAGE
{
    DIM msg AS STRING = "waiting"
    PRINT "Logger: ", msg
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

    PROBE amp1.output AS "Amp1 Out"
    PROBE mix.output  AS "Final Mix"

    SHARE gain_state BETWEEN amp1, amp2, mix
    EXPORT mix.output AS master_out
END BOARD`;

// ============================================================
//  REACT COMPONENTS
// ============================================================

function ModBlock({inst,sets,onDrill,state}) {
  const {x,y,w,h,instanceName:iname,moduleType:mtype,commMode}=inst;
  const cm=COMM[commMode]||COMM.DATAFLOW;
  const sv={};(sets||[]).forEach(s=>{if(s.mod===iname)sv[s.port]=s.value;});
  const executed=inst.executed;
  return (
    <g style={{cursor:"pointer"}} onDoubleClick={()=>onDrill(inst)}>
      <rect x={x+3} y={y+3} width={w} height={h} rx={5} fill="rgba(0,0,0,0.35)"/>
      <rect x={x} y={y} width={w} height={h} rx={5} fill={C.modFill} stroke={executed?C.success:cm.color} strokeWidth={executed?2:1.4} strokeDasharray={cm.dash}/>
      <rect x={x} y={y} width={w} height={34} rx={5} fill={C.modHeader}/><rect x={x} y={y+30} width={w} height={4} fill={C.modHeader}/>
      <text x={x+10} y={y+22} fill={C.modHeaderText} fontSize={13} fontWeight={700} fontFamily={FONT}>{iname}</text>
      <text x={x+w-8} y={y+22} fill={C.dim} textAnchor="end" fontSize={9} fontFamily={FONT}>{mtype}</text>
      {inst.inPorts.map((p,i)=>{
        const tc=TC[p.typeName]||C.portIn;
        return <g key={`i${i}`}>
          <circle cx={p.cx} cy={p.cy} r={5} fill={C.modFill} stroke={tc} strokeWidth={1.5}/>
          <line x1={p.cx-12} y1={p.cy} x2={p.cx-5} y2={p.cy} stroke={tc} strokeWidth={1.4}/>
          <text x={p.cx+9} y={p.cy+4} fill={C.modText} fontSize={9.5} fontFamily={FONT}>{p.name}</text>
          {p.liveValue!==undefined&&<text x={p.cx+9} y={p.cy+14} fill={C.setValue} fontSize={7.5} fontFamily={FONT} opacity={.85}>{String(p.liveValue).slice(0,22)}</text>}
        </g>;
      })}
      {inst.outPorts.map((p,i)=>{
        const tc=TC[p.typeName]||C.portOut;
        return <g key={`o${i}`}>
          <circle cx={p.cx} cy={p.cy} r={5} fill={C.modFill} stroke={tc} strokeWidth={1.5}/>
          <line x1={p.cx+5} y1={p.cy} x2={p.cx+12} y2={p.cy} stroke={tc} strokeWidth={1.4}/>
          <text x={p.cx-9} y={p.cy+4} fill={C.modText} textAnchor="end" fontSize={9.5} fontFamily={FONT}>{p.name}</text>
          {p.liveValue!==undefined&&<text x={p.cx-9} y={p.cy+14} fill={C.accent} textAnchor="end" fontSize={7.5} fontFamily={FONT} opacity={.85}>{String(p.liveValue).slice(0,22)}</text>}
        </g>;
      })}
      {executed&&<circle cx={x+w-12} cy={y+12} r={4} fill={C.success} opacity={.8}/>}
    </g>
  );
}

function Wire({wire}) {
  const {sx,sy,dx,dy,style}=wire;
  const msg=style==="message"; const color=msg?C.wireMsg:C.wireData;
  const cpO=Math.min(Math.abs(dx-sx)*.5,80);
  const path=`M${sx},${sy} C${sx+cpO},${sy} ${dx-cpO},${dy} ${dx},${dy}`;
  return <g>
    <path d={path} fill="none" stroke={color} strokeWidth={1.8} strokeDasharray={msg?"6,4":""} opacity={.75}/>
    <polygon points={`${dx},${dy} ${dx-7},${dy-3.5} ${dx-7},${dy+3.5}`} fill={color} opacity={.75}/>
    <circle r={2.5} fill={color} opacity={.9}><animateMotion dur={msg?"2.8s":"1.6s"} repeatCount="indefinite" path={path}/></circle>
  </g>;
}

function FlowNode({node}) {
  const {x,y,w,h,shape,label,fill,stroke,tc,n}=node;
  const tl=label.length>30?label.slice(0,28)+"…":label;
  const cx=x+w/2, cy=y+h/2;
  if(shape==="diamond"){const hw=w/2,hh=h/2+6;return <g><polygon points={`${cx},${cy-hh} ${cx+hw},${cy} ${cx},${cy+hh} ${cx-hw},${cy}`} fill={fill} stroke={stroke} strokeWidth={1.4}/><text x={cx} y={cy+4} fill={tc} textAnchor="middle" fontSize={9.5} fontFamily={FONT}>{tl}</text></g>;}
  if(shape==="octagon"){const s=Math.min(w,h)/2,ins=s*.38;const pts=[[cx-s+ins,cy-s],[cx+s-ins,cy-s],[cx+s,cy-s+ins],[cx+s,cy+s-ins],[cx+s-ins,cy+s],[cx-s+ins,cy+s],[cx-s,cy+s-ins],[cx-s,cy-s+ins]].map(p=>p.join(",")).join(" ");return <g><polygon points={pts} fill={fill} stroke={stroke} strokeWidth={2}/><text x={cx} y={cy+4} fill={tc} textAnchor="middle" fontSize={9.5} fontWeight={700} fontFamily={FONT}>{tl}</text></g>;}
  if(shape==="hexagon"){const indent=14;const pts=[[x+indent,y],[x+w-indent,y],[x+w,cy],[x+w-indent,y+h],[x+indent,y+h],[x,cy]].map(p=>p.join(",")).join(" ");return <g><polygon points={pts} fill={fill} stroke={stroke} strokeWidth={1.4}/><text x={cx} y={cy+4} fill={tc} textAnchor="middle" fontSize={9.5} fontFamily={FONT}>{tl}</text></g>;}
  if(shape==="loop"){return <g><rect x={x} y={y} width={w} height={h} rx={8} fill={fill} stroke={stroke} strokeWidth={1.4}/><path d={`M${x+w-18},${y+6}a5,5 0 1 1 0,10`} fill="none" stroke={stroke} strokeWidth={1.2}/><text x={x+10} y={cy+4} fill={tc} fontSize={9.5} fontFamily={FONT}>{tl}</text></g>;}
  if(shape==="label"){return <g><line x1={x} y1={cy} x2={x+w} y2={cy} stroke={stroke} strokeWidth={.8} strokeDasharray="4,2"/><circle cx={x+6} cy={cy} r={3.5} fill={stroke}/><text x={x+14} y={cy+4} fill={tc} fontSize={10} fontWeight={600} fontFamily={FONT}>{label}</text></g>;}
  if(shape==="asm"){const body=n?.body||[];return <g><rect x={x} y={y} width={w} height={h} rx={3} fill={fill} stroke={stroke} strokeWidth={1.4} strokeDasharray="4,2"/><rect x={x} y={y} width={w} height={20} rx={3} fill={stroke} fillOpacity={.18}/><text x={x+8} y={y+14} fill={tc} fontSize={9.5} fontWeight={700} fontFamily={FONT}>{tl}</text>{body.slice(0,12).map((instr,i)=>{let txt=instr.t==="AsmInstr"?`${instr.op} ${(instr.operands||[]).map(o=>o.v||"#(...)").join(", ")}`:instr.t==="AsmLabelDef"?`.${instr.name}:`:instr.t==="AsmEquate"?`.${instr.name} EQU`:instr.t||"?";return <text key={i} x={x+12} y={y+36+i*20} fill={instr.t==="AsmLabelDef"?C.flowLabel:C.flowAsmText} fontSize={8.5} fontFamily={FONT} opacity={.9}>{txt.slice(0,32)}</text>;})}</g>;}
  return <g><rect x={x} y={y} width={w} height={h} rx={3} fill={fill} stroke={stroke} strokeWidth={1}/><text x={x+8} y={cy+4} fill={tc} fontSize={9.5} fontFamily={FONT}>{tl}</text></g>;
}

function FlowArrow({from,to}) {
  if(!from||!to||from.y+from.h>=to.y) return null;
  const sx=from.x+from.w/2,sy=from.y+from.h,dx=to.x+to.w/2,dy=to.y;
  if(Math.abs(sx-dx)>4){const my=(sy+dy)/2;return <g><path d={`M${sx},${sy}L${sx},${my}L${dx},${my}L${dx},${dy}`} fill="none" stroke={C.dim} strokeWidth={1}/><polygon points={`${dx},${dy} ${dx-3.5},${dy-5} ${dx+3.5},${dy-5}`} fill={C.dim}/></g>;}
  return <g><line x1={sx} y1={sy} x2={dx} y2={dy} stroke={C.dim} strokeWidth={1}/><polygon points={`${dx},${dy} ${dx-3.5},${dy-5} ${dx+3.5},${dy-5}`} fill={C.dim}/></g>;
}

// ============================================================
//  MAIN APP
// ============================================================
function OrbIDE() {
  const [source, setSource] = useState(SAMPLE);
  const [view, setView] = useState("board");
  const [selectedMod, setSelectedMod] = useState(null);
  const [state, setState] = useState(null);
  const [ast, setAst] = useState(null);
  const [output, setOutput] = useState([]);
  const [error, setError] = useState(null);
  const [showEditor, setShowEditor] = useState(true);

  const runProgram = useCallback(() => {
    setOutput([]); setError(null); setState(null);
    try {
      const parsed = orbParse(source);
      setAst(parsed);
      const lines = [];
      const interp = new OrbInterpreter((...a) => lines.push(a.join(" ")));
      interp.run(source);
      setOutput(lines);
      setState(interp.getState());
      if (interp.error) setError(interp.error);
    } catch (e) {
      setError(e.message);
      setOutput(prev => [...prev, `ERROR: ${e.message}`]);
    }
  }, [source]);

  useEffect(() => { runProgram(); }, []);

  const modules = useMemo(() => {
    if (!ast) return {};
    const m = {};
    ast.body.filter(n => n.t === "ModuleDecl").forEach(n => m[n.name] = n);
    return m;
  }, [ast]);

  const board = useMemo(() => ast?.body?.find(n => n.t === "BoardDecl"), [ast]);
  const boardLayout = useMemo(() => board ? layoutBoard(board, modules, state) : null, [board, modules, state]);
  const flowLayout = useMemo(() => selectedMod ? layoutFlow(selectedMod.body) : null, [selectedMod]);

  const handleDrill = useCallback((inst) => {
    const mod = modules[inst.moduleType];
    if (mod) { setSelectedMod(mod); setView("module"); }
  }, [modules]);

  const svgW = view === "board" ? (boardLayout?.bounds.w || 700) : (flowLayout?.bounds.w || 400);
  const svgH = view === "board" ? (boardLayout?.bounds.h || 450) : (flowLayout?.bounds.h || 600);

  const stateVars = useMemo(() => {
    if (!state?.instances) return [];
    return Object.entries(state.instances).flatMap(([name, inst]) =>
      Object.entries(inst.vars).filter(([k]) => !inst.inPorts[k] && !inst.outPorts[k]).map(([k, v]) => ({ inst: name, name: k, value: String(v).slice(0, 30) }))
    );
  }, [state]);

  return (
    <div style={{ width:"100%",height:"100vh",background:C.bg,display:"flex",flexDirection:"column",fontFamily:FONT,color:C.modText,overflow:"hidden" }}>
      {/* Header */}
      <div style={{ height:40,minHeight:40,background:C.headerBg,borderBottom:`1px solid ${C.panelBorder}`,display:"flex",alignItems:"center",padding:"0 12px",gap:10 }}>
        <div style={{ fontSize:14,fontWeight:800,color:C.accent,letterSpacing:1.5 }}>ORB</div>
        <div style={{ width:1,height:20,background:C.panelBorder }}/>
        {view==="board" ? (
          <span style={{fontSize:12,color:C.accent}}>{board?.name||"board"}</span>
        ) : (
          <div style={{display:"flex",gap:4,fontSize:12,alignItems:"center"}}>
            <span style={{color:C.dim,cursor:"pointer",textDecoration:"underline"}} onClick={()=>{setView("board");setSelectedMod(null);}}>{board?.name||"board"}</span>
            <span style={{color:C.panelBorder}}> › </span>
            <span style={{color:C.accent}}>{selectedMod?.name}</span>
          </div>
        )}
        <div style={{flex:1}}/>
        <button onClick={()=>setShowEditor(!showEditor)} style={{background:"none",border:`1px solid ${C.panelBorder}`,color:C.dim,padding:"3px 8px",borderRadius:4,cursor:"pointer",fontSize:10}}>
          {showEditor?"Hide Editor":"Show Editor"}
        </button>
        <button onClick={runProgram} style={{background:C.success,border:"none",color:"#000",padding:"4px 14px",borderRadius:4,cursor:"pointer",fontSize:11,fontWeight:700,fontFamily:FONT}}>
          ▶ RUN
        </button>
        <span style={{fontSize:8,color:C.dim,textTransform:"uppercase",letterSpacing:2}}>
          {view==="board"?"Board View":"Module Flow"}
        </span>
      </div>

      <div style={{ flex:1,display:"flex",overflow:"hidden" }}>
        {/* Editor Panel */}
        {showEditor && (
          <div style={{ width:340,minWidth:280,borderRight:`1px solid ${C.panelBorder}`,display:"flex",flexDirection:"column",background:C.panel }}>
            <div style={{padding:"6px 10px",fontSize:9,color:C.dim,textTransform:"uppercase",letterSpacing:1.5,borderBottom:`1px solid ${C.panelBorder}`}}>Source</div>
            <textarea
              value={source} onChange={e=>setSource(e.target.value)}
              spellCheck={false}
              style={{ flex:1,background:"transparent",color:C.modText,border:"none",padding:"8px 10px",fontSize:11,fontFamily:FONT,resize:"none",outline:"none",lineHeight:1.5,tabSize:4 }}
            />
          </div>
        )}

        {/* SVG Canvas */}
        <div style={{ flex:1,overflow:"auto" }}>
          <svg width={Math.max(svgW,600)} height={Math.max(svgH,400)} style={{display:"block"}}>
            {/* Grid */}
            <defs><pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse"><circle cx="10" cy="10" r=".5" fill={C.panelBorder}/></pattern></defs>
            <rect width="100%" height="100%" fill="url(#grid)"/>

            {view==="board"&&boardLayout&&<g>
              {boardLayout.shares.map((s,i)=>{
                const ps=s.modules.map(m=>boardLayout.insts[m]).filter(Boolean);
                if(ps.length<2)return null;
                const ax=ps.reduce((s,p)=>s+p.x+p.w/2,0)/ps.length;
                const my=Math.max(...ps.map(p=>p.y+p.h));
                return <g key={i}><rect x={ax-48} y={my+20} width={96} height={24} rx={3} fill={C.sharedFill} stroke={C.sharedStroke} strokeWidth={1} strokeDasharray="3,3"/><text x={ax} y={my+36} fill={C.sharedStroke} textAnchor="middle" fontSize={8} fontWeight={600} fontFamily={FONT}>{s.stateName}</text>{ps.map((p,j)=><line key={j} x1={ax} y1={my+20} x2={p.x+p.w/2} y2={p.y+p.h} stroke={C.sharedStroke} strokeWidth={.6} strokeDasharray="3,3" opacity={.4}/>)}</g>;
              })}
              {boardLayout.wires.map((w,i)=><Wire key={`w${i}`} wire={w}/>)}
              {boardLayout.routes.map((r,i)=><Wire key={`r${i}`} wire={r}/>)}
              {Object.values(boardLayout.insts).map(inst=><ModBlock key={inst.instanceName} inst={inst} sets={boardLayout.sets} onDrill={handleDrill} state={state}/>)}
              {boardLayout.probes.map((p,i)=>{
                const inst=boardLayout.insts[p.mod];if(!inst)return null;
                const pt=inst.outPorts?.find(pp=>pp.name===p.port);if(!pt)return null;
                return <g key={i}><circle cx={pt.cx+18} cy={pt.cy} r={6} fill="none" stroke={C.probe} strokeWidth={1}/><circle cx={pt.cx+18} cy={pt.cy} r={1.5} fill={C.probe}/><line x1={pt.cx+22} y1={pt.cy+4} x2={pt.cx+28} y2={pt.cy+10} stroke={C.probe} strokeWidth={1.2} strokeLinecap="round"/>{p.label&&<text x={pt.cx+32} y={pt.cy+12} fill={C.probe} fontSize={7.5} fontFamily={FONT}>{p.label}</text>}</g>;
              })}
              {boardLayout.exports.map((e,i)=>{
                const inst=boardLayout.insts[e.mod];if(!inst)return null;
                const pt=inst.outPorts?.find(pp=>pp.name===e.port);if(!pt)return null;
                return <g key={i}><line x1={pt.cx+12} y1={pt.cy} x2={pt.cx+44} y2={pt.cy} stroke={C.setValue} strokeWidth={1.4}/><polygon points={`${pt.cx+44},${pt.cy} ${pt.cx+39},${pt.cy-3.5} ${pt.cx+39},${pt.cy+3.5}`} fill={C.setValue}/><text x={pt.cx+48} y={pt.cy+4} fill={C.setValue} fontSize={8} fontWeight={600} fontFamily={FONT}>⤳ {e.extName}</text></g>;
              })}
            </g>}

            {view==="module"&&flowLayout&&<g>
              {flowLayout.nodes.map((n,i)=>{const next=flowLayout.nodes[i+1];return <FlowArrow key={`a${i}`} from={n} to={next}/>;})}
              {flowLayout.nodes.map((n,i)=><FlowNode key={`n${i}`} node={n}/>)}
              <text x={16} y={28} fill={C.modHeaderText} fontSize={13} fontWeight={700} fontFamily={FONT}>MODULE {selectedMod?.name}</text>
            </g>}
          </svg>
        </div>

        {/* Right Panel: Output + State */}
        <div style={{ width:260,minWidth:200,borderLeft:`1px solid ${C.panelBorder}`,display:"flex",flexDirection:"column",background:C.panel }}>
          <div style={{padding:"6px 10px",fontSize:9,color:C.dim,textTransform:"uppercase",letterSpacing:1.5,borderBottom:`1px solid ${C.panelBorder}`}}>Output</div>
          <div style={{ flex:1,overflow:"auto",padding:"6px 10px",fontSize:10,lineHeight:1.6 }}>
            {output.map((line,i)=>(
              <div key={i} style={{color:line.startsWith("ERROR")?C.danger:line.startsWith("[INSPECT")?C.flowInspect:C.modText}}>
                {line}
              </div>
            ))}
            {error&&<div style={{color:C.danger,marginTop:6,fontWeight:700}}>{error}</div>}
            {state&&!error&&<div style={{color:C.success,marginTop:6,fontSize:9}}>✓ {Object.keys(state.instances).length} instances · {state.output.length} outputs</div>}
          </div>

          <div style={{borderTop:`1px solid ${C.panelBorder}`,padding:"6px 10px",fontSize:9,color:C.dim,textTransform:"uppercase",letterSpacing:1.5}}>State</div>
          <div style={{ height:200,overflow:"auto",padding:"4px 10px",fontSize:9.5,lineHeight:1.7 }}>
            {state&&Object.entries(state.instances).map(([name,inst])=>(
              <div key={name} style={{marginBottom:6}}>
                <div style={{color:C.accent,fontWeight:700}}>{name} <span style={{color:C.dim,fontWeight:400}}>({inst.moduleType})</span></div>
                {Object.entries(inst.outPorts).map(([pn,pv])=>(
                  <div key={pn} style={{paddingLeft:8,color:C.portOut}}>OUT {pn} = <span style={{color:C.modText}}>{String(pv.value).slice(0,28)}</span></div>
                ))}
                {Object.entries(inst.vars).filter(([k])=>!inst.inPorts[k]&&!inst.outPorts[k]).slice(0,4).map(([k,v])=>(
                  <div key={k} style={{paddingLeft:8,color:C.dim}}>{k} = <span style={{color:C.modText}}>{String(v).slice(0,28)}</span></div>
                ))}
              </div>
            ))}
            {state?.registers&&Object.entries(state.registers).filter(([k])=>k!=="FLAGS").map(([k,v])=>(
              <div key={k} style={{color:C.flowAsmText,paddingLeft:4}}>{k} = {v}</div>
            ))}
          </div>
        </div>
      </div>

      {/* Status bar */}
      <div style={{ height:24,minHeight:24,background:C.headerBg,borderTop:`1px solid ${C.panelBorder}`,display:"flex",alignItems:"center",padding:"0 12px",fontSize:9,color:C.dim,gap:16 }}>
        <span>{state? `${Object.keys(state.instances).length} instances · ${state.output.length} outputs`:"Ready"}</span>
        {state?.halted&&<span style={{color:C.danger}}>HALTED: {state.haltMsg}</span>}
        <span style={{flex:1}}/>
        <span style={{color:C.panelBorder}}>ORB v0.1 · Live Interpreter</span>
      </div>
    </div>
  );
}
