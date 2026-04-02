# Orb

**A visual programming language where programs are circuit schematics.**

Orb is an interpreted language where modules are components, wires carry data, and the program is a living circuit diagram. Write code on the left, watch data flow through the schematic on the right. Double-click a module to drill into its flowchart. Inline assembly shares variables with the high-level code — no separate worlds.

**[→ Try the live demo](https://aviato-dynamics.github.io/orb-demo/)**

---

## What You're Looking At

The IDE has three panels:

- **Left** — code editor. The sample program defines two amplifier modules, a mixer, and a logger, wires them together on a board, and runs.
- **Centre** — the board view. Module instances are blocks with typed pins. Blue wires carry vector data. Animated dots show data flowing. Green indicators show which modules have executed. Double-click any block to see its internal flowchart.
- **Right** — output console (top) and live state inspector (bottom). Shows PRINT output, INSPECT dumps, and every module's port values and variables after execution.

Hit **▶ RUN** to re-execute after editing. The interpreter runs entirely in the browser.

## The Language

Orb is a BASIC-C hybrid with module composition and BBC BASIC-style inline assembly.

### Modules and Boards

Programs are built by defining modules (components) and wiring them together on boards (circuit diagrams):

```
MODULE amplifier DATAFLOW
PORTS
    IN  signal AS VEC[4]
    IN  gain   AS FLOAT
    OUT output AS VEC[4]
END PORTS
{
    DIM temp AS VEC[4]
    VLOAD temp FROM signal

    FOR i = 0 TO 3
        temp[i] = temp[i] * gain
    NEXT i

    VSTORE temp TO output
}
END MODULE

BOARD main
    PLACE amplifier AS amp1
    PLACE amplifier AS amp2
    PLACE mixer     AS mix

    WIRE amp1.output TO mix.input_a
    WIRE amp2.output TO mix.input_b

    SET amp1.gain = 2.0
    SET amp1.signal = <1.0, 2.0, 3.0, 4.0>
END BOARD
```

Modules execute in dependency order — upstream modules run before downstream ones. The board handles the wiring. You don't manage data flow manually.

### Three Communication Modes

Each module chooses how it talks to others:

- **DATAFLOW** — solid wires, continuous data flow, automatic execution order
- **MESSAGE** — dashed wires, discrete events via `SEND` / `RECV`
- **SHARED** — shared state with explicit `LOCK` / `UNLOCK` blocks

### Inline Assembly

Orb's inline assembly is modelled on BBC BASIC's assembler. The key feature is **variable bridging** — assembly and the high-level language share the same variables:

```
DIM gain AS FLOAT = 2.5
[
    ALU D0, D0, D0, XOR        // D0 = 0
    MOV D1, gain                // reads the Orb variable
    ALUI D2, D0, 10             // D2 = 10
    ALU D1, D1, D2, ADD         // D1 = gain + 10
    MOV gain, D1                // writes back to Orb variable
]
PRINT gain                      // prints 12.5
```

There are no separate worlds. The assembler is part of the language, not a separate tool. Orb control flow (FOR, IF) works inside assembly blocks — the same two-pass model BBC BASIC used.

### Types

Four types, deliberately minimal:

| Type | Description | Literal |
|------|-------------|---------|
| `INT` | 32-bit signed integer | `42` |
| `FLOAT` | 64-bit float | `3.14` |
| `STRING` | Text | `"hello"` |
| `VEC` | Fixed-width float vector | `<1.0, 2.0, 3.0, 4.0>` |

### Vector Operations

First-class SIMD-style operations:

```
VADD signal_a, signal_b INTO result
VLOAD temp FROM raw_input
VSTORE processed TO output_buffer
VDOT coefficients INTO dot_product
```

### Control Flow

```
IF gain > 1.0 THEN
    PRINT "amplifying"
ELIF gain == 1.0 THEN
    PRINT "unity"
ELSE
    PRINT "attenuating"
END IF

FOR i = 0 TO 7
    output[i] = input[i] * scale
NEXT i

WHILE running
    GOSUB process_frame
WEND

@process_frame:
    // ...
RETURN
```

### Visual Mapping

Every construct has a defined visual shape in the flowchart view:

| Code | Visual |
|------|--------|
| Assignment | Rectangle |
| `IF` / `ELIF` | Diamond |
| `FOR` / `WHILE` | Rounded box with loop arrow |
| `@label` | Anchor dot with dashed line |
| `HALT` | Red octagon |
| `INSPECT` | Purple hexagon |
| `ASM` block | Indigo shaded region |
| `SEND` / `RECV` | Orange envelope icon |

## Running Locally

The demo is a single React component. To run it locally:

```bash
# Clone
git clone https://github.com/Aviato-Dynamics/orb-demo.git
cd orb-demo

# The IDE is a single .jsx file — drop it into any React project,
# or use the hosted version at the link above.
```

No build step is required for the hosted version. The interpreter runs entirely client-side.

## Project Status

The Orb language toolchain includes:

- ✅ Formal grammar (EBNF)
- ✅ Lexer / tokeniser (Python)
- ✅ Recursive descent parser (Python)
- ✅ AST node definitions (40+ node types)
- ✅ Interpreter (Python and JavaScript)
- ✅ Interactive SVG board + flowchart renderer (React)
- ✅ Module system with three communication modes
- ✅ BBC BASIC-style inline assembly with variable bridging
- ✅ Vector operations
- 🔧 Register-level geometric view (Layer 3)
- 🔧 Drag-and-drop visual editing
- 🔧 Step-through execution with live highlighting

## The Name

**Orb** — the programming language and visual environment.

## Licence

Language specification and demo: MIT.

Additional components under development are subject to separate licensing terms that will be announced at a later date.

## Contact

Aviato Dynamics

---

*Orb is under active development. A more detailed technical paper is forthcoming.*
