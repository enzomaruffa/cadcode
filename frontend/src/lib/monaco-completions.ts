import * as monaco from "monaco-editor";
import { HTTP_URL } from "../config";
import { useStore } from "./store";

// Import-aware autocomplete for the Python editor. Three layers:
//   1. build123d + cadcode DSL (curated names, snippets, docs);
//   2. the live catalog (/completions): every project's parts (real signatures
//      + docstrings), scenes, constants, and the global library parts;
//   3. AUTO-IMPORT: typing a part name anywhere offers `part()` and inserts the
//      right import line at the top of the file if it's missing — so wiring one
//      part into another is a single completion-accept.
// Import lines themselves are understood too: `from parts.` lists your parts,
// `from projects.` walks other projects, `from lib.parts import` the library,
// `from project import` your constants, `from build123d import` the API.

const K = monaco.languages.CompletionItemKind;
const SNIPPET = monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet;

interface Item {
  label: string;
  insert: string;
  kind: monaco.languages.CompletionItemKind;
  detail?: string;
  doc?: string;
  snippet?: boolean;
  sortText?: string;
  extraEdits?: monaco.editor.ISingleEditOperation[];
}

// --- the live catalog (parts / projects / constants), cached with a short TTL --

interface PartInfo {
  name: string;
  params: string;
  doc: string;
}
interface ProjectInfo {
  name: string;
  parts: PartInfo[];
  scenes: string[];
  constants: string[];
}
interface LibPart {
  name: string;
  signature: string;
  doc: string;
}

let catalog: { projects: ProjectInfo[]; lib_parts: LibPart[] } = { projects: [], lib_parts: [] };
let catalogAt = 0;
let inflight: Promise<void> | null = null;

function refreshCatalog(force = false): Promise<void> {
  if (!force && Date.now() - catalogAt < 15_000) return Promise.resolve();
  if (inflight) return inflight;
  inflight = fetch(`${HTTP_URL}/completions`)
    .then((r) => r.json())
    .then((d: { projects?: ProjectInfo[]; lib_parts?: LibPart[] }) => {
      catalog = { projects: d.projects ?? [], lib_parts: d.lib_parts ?? [] };
      catalogAt = Date.now();
    })
    .catch(() => void 0)
    .finally(() => {
      inflight = null;
    });
  return inflight;
}
refreshCatalog(true);

// Global design tokens (lib.design) for the default context.
let designTokens: string[] = [];
fetch(`${HTTP_URL}/design`)
  .then((r) => r.json())
  .then((d: { tokens?: { name: string }[] }) => {
    designTokens = (d.tokens ?? []).map((t) => t.name);
  })
  .catch(() => void 0);

// Refresh the catalog when the active project changes (new parts/constants).
let lastProject = useStore.getState().activeProject;
useStore.subscribe((state) => {
  if (state.activeProject !== lastProject) {
    lastProject = state.activeProject;
    void refreshCatalog(true);
  }
});

// --- the REAL build123d API (introspected server-side) -------------------------
// Powers hover docs, parameter hints, and full-API completion — signatures come
// from the installed package, so param order is always true (Box: length, width,
// height…). Static per deploy → fetched once.

interface B3dParam {
  name: string;
  default?: string;
}
interface B3dEntry {
  kind: string; // function | class | enum | value
  signature: string;
  doc: string;
  params?: B3dParam[];
  members?: string[];
}

let b3dDocs: Record<string, B3dEntry> = {};
fetch(`${HTTP_URL}/docs/build123d`)
  .then((r) => r.json())
  .then((d: Record<string, B3dEntry>) => {
    b3dDocs = d ?? {};
  })
  .catch(() => void 0);

/** Markdown hover/completion doc for an API entry. */
function b3dMarkdown(name: string, e: B3dEntry): string {
  const sig = e.signature && e.signature !== name ? e.signature : name;
  return `\`\`\`python\n${sig}\n\`\`\`\n\n${e.doc || ""}`;
}

/** Auto-generate a snippet from the REAL signature: required params become
 *  placeholders (capped at 5), all-optional callables get a bare cursor. */
function b3dSnippet(name: string, e: B3dEntry): { insert: string; snippet: boolean } {
  if (e.kind === "enum" || e.kind === "value") return { insert: name, snippet: false };
  const required = (e.params ?? []).filter((p) => !("default" in p)).slice(0, 5);
  if (!required.length) return { insert: `${name}($1)`, snippet: true };
  return { insert: `${name}(${required.map((p, i) => `\${${i + 1}:${p.name}}`).join(", ")})`, snippet: true };
}

// --- curated build123d + cadcode names ---------------------------------------

const TOP_LEVEL: Item[] = [
  // primitives
  {
    label: "Box",
    insert: "Box(${1:length}, ${2:width}, ${3:height})",
    kind: K.Function,
    detail: "solid box",
    snippet: true,
  },
  {
    label: "Cylinder",
    insert: "Cylinder(${1:radius}, ${2:height})",
    kind: K.Function,
    detail: "solid cylinder",
    snippet: true,
  },
  { label: "Sphere", insert: "Sphere(${1:radius})", kind: K.Function, detail: "solid sphere", snippet: true },
  { label: "Cone", insert: "Cone(${1:bottom_radius}, ${2:top_radius}, ${3:height})", kind: K.Function, snippet: true },
  { label: "Torus", insert: "Torus(${1:major_radius}, ${2:minor_radius})", kind: K.Function, snippet: true },
  {
    label: "Wedge",
    insert: "Wedge(${1:dx}, ${2:dy}, ${3:dz}, ${4:xmin}, ${5:zmin}, ${6:xmax}, ${7:zmax})",
    kind: K.Function,
    snippet: true,
  },
  // 2D
  { label: "Rectangle", insert: "Rectangle(${1:width}, ${2:height})", kind: K.Function, snippet: true },
  { label: "Circle", insert: "Circle(${1:radius})", kind: K.Function, snippet: true },
  { label: "Ellipse", insert: "Ellipse(${1:x_radius}, ${2:y_radius})", kind: K.Function, snippet: true },
  { label: "RegularPolygon", insert: "RegularPolygon(${1:radius}, ${2:side_count})", kind: K.Function, snippet: true },
  { label: "Polyline", insert: "Polyline(${1:points})", kind: K.Function, snippet: true },
  { label: "Spline", insert: "Spline(${1:points})", kind: K.Function, snippet: true },
  { label: "Text", insert: 'Text("${1:hi}", ${2:font_size})', kind: K.Function, snippet: true },
  // operations
  {
    label: "fillet",
    insert: "fillet(${1:edges}, radius=${2:2})",
    kind: K.Function,
    detail: "round edges",
    snippet: true,
  },
  {
    label: "chamfer",
    insert: "chamfer(${1:edges}, length=${2:1})",
    kind: K.Function,
    detail: "bevel edges",
    snippet: true,
  },
  { label: "extrude", insert: "extrude(${1:sketch}, amount=${2:5})", kind: K.Function, snippet: true },
  { label: "revolve", insert: "revolve(${1:sketch})", kind: K.Function, snippet: true },
  { label: "loft", insert: "loft([${1:sketches}])", kind: K.Function, snippet: true },
  { label: "sweep", insert: "sweep(${1:sketch}, path=${2:path})", kind: K.Function, snippet: true },
  {
    label: "offset",
    insert: "offset(${1:solid}, amount=-${2:wall}, openings=[${3:face}])",
    kind: K.Function,
    detail: "shell / hollow",
    doc: "Negative amount + openings hollows a solid (thin-wall part). The openings faces are removed.",
    snippet: true,
  },
  { label: "mirror", insert: "mirror(${1:obj}, about=Plane.${2:YZ})", kind: K.Function, snippet: true },
  { label: "scale", insert: "scale(${1:obj}, by=${2:2})", kind: K.Function, snippet: true },
  { label: "split", insert: "split(${1:obj}, bisect_by=Plane.${2:XZ})", kind: K.Function, snippet: true },
  { label: "make_face", insert: "make_face(${1:edges})", kind: K.Function, snippet: true },
  { label: "Hole", insert: "Hole(${1:radius}, depth=${2:10})", kind: K.Function, snippet: true },
  { label: "Compound", insert: "Compound(children=[${1:parts}])", kind: K.Class, snippet: true },
  // placement
  { label: "Pos", insert: "Pos(${1:x}, ${2:y}, ${3:z})", kind: K.Function, detail: "translate", snippet: true },
  { label: "Rot", insert: "Rot(${1:x}, ${2:y}, ${3:z})", kind: K.Function, detail: "rotate (deg)", snippet: true },
  { label: "Location", insert: "Location((${1:x}, ${2:y}, ${3:z}))", kind: K.Function, snippet: true },
  { label: "Locations", insert: "Locations(${1:points})", kind: K.Function, snippet: true },
  {
    label: "GridLocations",
    insert: "GridLocations(${1:x_spacing}, ${2:y_spacing}, ${3:x_count}, ${4:y_count})",
    kind: K.Function,
    snippet: true,
  },
  { label: "PolarLocations", insert: "PolarLocations(${1:radius}, ${2:count})", kind: K.Function, snippet: true },
  // joints (drive the articulated physics playground)
  {
    label: "RigidJoint",
    insert: 'RigidJoint("${1:name}", ${2:part}, Location((${3:x}, ${4:y}, ${5:z})))',
    kind: K.Function,
    detail: "fixed anchor",
    doc: "A named rigid anchor on a part. Connect with `a.joints['x'].connect_to(b.joints['y'])`.",
    snippet: true,
  },
  {
    label: "RevoluteJoint",
    insert:
      'RevoluteJoint("${1:pivot}", ${2:part}, axis=Axis((${3:x}, ${4:y}, ${5:z}), (${6:0}, ${7:1}, ${8:0})), angular_range=(${9:0}, ${10:120}))',
    kind: K.Function,
    detail: "hinge",
    doc: "A hinge: one rotational DOF about `axis`, limited to `angular_range` (deg). Becomes a live hinge in the physics playground.",
    snippet: true,
  },
  {
    label: "LinearJoint",
    insert:
      'LinearJoint("${1:slide}", ${2:part}, axis=Axis((${3:0}, ${4:0}, ${5:0}), (${6:1}, ${7:0}, ${8:0})), linear_range=(${9:0}, ${10:40}))',
    kind: K.Function,
    detail: "slider",
    snippet: true,
  },
  {
    label: "connect_to",
    insert: "joints['${1:a}'].connect_to(${2:other}.joints['${3:b}'])",
    kind: K.Method,
    detail: "snap joints together",
    snippet: true,
  },
  // enums / axes
  { label: "Axis", insert: "Axis", kind: K.Enum, detail: "Axis.X / Y / Z" },
  { label: "Plane", insert: "Plane", kind: K.Enum, detail: "Plane.XY / XZ / YZ" },
  { label: "Mode", insert: "Mode", kind: K.Enum, detail: "ADD / SUBTRACT / INTERSECT" },
  { label: "Align", insert: "Align", kind: K.Enum, detail: "MIN / CENTER / MAX" },
  { label: "Color", insert: 'Color("${1:#c9a06a}")', kind: K.Class, snippet: true },
  // cadcode DSL
  {
    label: "show",
    insert: 'show(${1:part}, name="${2:part}", color="${3:#c9a06a}")',
    kind: K.Function,
    detail: "render",
    snippet: true,
  },
  { label: "show_object", insert: "show_object(${1:part})", kind: K.Function, snippet: true },
  {
    label: "require",
    insert: 'require(${1:condition}, "${2:message}")',
    kind: K.Function,
    detail: "spec (CAD-as-TDD)",
    snippet: true,
  },
  { label: "Range", insert: "Range(${1:min}, ${2:max})", kind: K.Function, detail: "slider range", snippet: true },
  { label: "Annotated", insert: "Annotated[${1:float}, Range(${2:min}, ${3:max})]", kind: K.Class, snippet: true },
];

// Names offerable inside `from build123d import …` (plain, no snippets).
const BUILD123D_NAMES = [
  ...new Set([
    ...TOP_LEVEL.filter(
      (t) => !["show", "show_object", "require", "Range", "Annotated", "connect_to"].includes(t.label),
    ).map((t) => t.label),
    "BuildPart",
    "BuildSketch",
    "BuildLine",
    "Part",
    "Sketch",
    "Curve",
    "Vector",
    "Edge",
    "Face",
    "Solid",
    "Wire",
    "CounterBoreHole",
    "CounterSinkHole",
    "BallJoint",
    "CylindricalJoint",
    "export_stl",
    "export_step",
    "import_step",
    "import_stl",
  ]),
].sort();

const METHODS: Item[] = [
  { label: "faces", insert: "faces()", kind: K.Method },
  { label: "edges", insert: "edges()", kind: K.Method },
  { label: "vertices", insert: "vertices()", kind: K.Method },
  { label: "solids", insert: "solids()", kind: K.Method },
  { label: "wires", insert: "wires()", kind: K.Method },
  { label: "sort_by", insert: "sort_by(Axis.${1:Z})", kind: K.Method, snippet: true },
  { label: "filter_by", insert: "filter_by(${1:Plane.XY})", kind: K.Method, snippet: true },
  { label: "group_by", insert: "group_by(Axis.${1:Z})", kind: K.Method, snippet: true },
  { label: "bounding_box", insert: "bounding_box()", kind: K.Method },
  { label: "center", insert: "center()", kind: K.Method },
  { label: "joints", insert: "joints", kind: K.Property, detail: "named joints dict" },
  { label: "connect_to", insert: "connect_to(${1:other}.joints['${2:name}'])", kind: K.Method, snippet: true },
  { label: "color", insert: "color", kind: K.Property, detail: "part display color" },
];

const AXIS_MEMBERS: Item[] = ["X", "Y", "Z"].map((a) => ({ label: a, insert: a, kind: K.EnumMember }));
const PLANE_MEMBERS: Item[] = ["XY", "XZ", "YZ", "front", "top", "right"].map((p) => ({
  label: p,
  insert: p,
  kind: K.EnumMember,
}));

// --- auto-import machinery ----------------------------------------------------

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function hasImport(model: monaco.editor.ITextModel, module: string, name: string): boolean {
  const re = new RegExp(`^\\s*from\\s+${escapeRe(module)}\\s+import\\s+.*\\b${escapeRe(name)}\\b`, "m");
  return re.test(model.getValue());
}

/** Edit that inserts `importLine` after the file's last top-level import (or at
 *  the very top). Attached to a completion via additionalTextEdits. */
function importEdit(model: monaco.editor.ITextModel, importLine: string): monaco.editor.ISingleEditOperation[] {
  const lines = model.getValue().split("\n");
  let last = 0;
  for (let i = 0; i < lines.length; i++) {
    if (/^(?:from|import)\s/.test(lines[i])) last = i + 1;
  }
  return [{ range: new monaco.Range(last + 1, 1, last + 1, 1), text: importLine + "\n" }];
}

/** Every known part as an auto-importing completion, scoped to what will
 *  actually run: a project file gets its own parts + other projects + lib; the
 *  scratch buffer gets lib only (projects aren't on the kernel's path there). */
function partCompletions(model: monaco.editor.ITextModel): Item[] {
  const s = useStore.getState();
  const doc = s.docs.find((d) => d.id === s.activeDocId);
  const origin = doc?.origin;
  const own = origin?.project ?? null;
  const items: Item[] = [];

  const add = (part: { name: string; sig: string; doc: string }, module: string, from: string, sort: string) => {
    const missing = !hasImport(model, module, part.name);
    items.push({
      label: part.name,
      insert: `${part.name}($1)`,
      kind: K.Function,
      detail: `part · ${from}${missing ? " · auto-import" : ""}`,
      doc: `\`${part.sig || part.name + "()"}\`\n\n${part.doc}${missing ? `\n\n_adds:_ \`from ${module} import ${part.name}\`` : ""}`,
      snippet: true,
      sortText: sort + part.name,
      extraEdits: missing ? importEdit(model, `from ${module} import ${part.name}`) : undefined,
    });
  };

  if (own) {
    const proj = catalog.projects.find((p) => p.name === own);
    for (const part of proj?.parts ?? []) {
      if (origin?.kind === "part" && origin.name === part.name) continue; // not into itself
      add({ name: part.name, sig: `${part.name}(${part.params})`, doc: part.doc }, `parts.${part.name}`, own, "1");
    }
    for (const p of catalog.projects) {
      if (p.name === own) continue;
      for (const part of p.parts) {
        add(
          { name: part.name, sig: `${part.name}(${part.params})`, doc: part.doc },
          `projects.${p.name}.parts.${part.name}`,
          p.name,
          "3",
        );
      }
    }
  }
  for (const lp of catalog.lib_parts) {
    add({ name: lp.name, sig: lp.signature, doc: lp.doc }, "lib.parts", "library", "2");
  }
  return items;
}

// --- import-line contexts -------------------------------------------------------

function importContext(before: string, model: monaco.editor.ITextModel): Item[] | null {
  const s = useStore.getState();
  const doc = s.docs.find((d) => d.id === s.activeDocId);
  const own = doc?.origin?.project ?? s.activeProject;
  const ownProj = catalog.projects.find((p) => p.name === own);
  const plain = (labels: string[], kind = K.Module, detail?: string): Item[] =>
    labels.map((l) => ({ label: l, insert: l, kind, detail }));

  let m: RegExpMatchArray | null;

  // from |
  if (/^\s*from\s+$/.test(before)) {
    return [
      ...plain(["project"], K.Module, "this project's constants"),
      ...plain(["parts"], K.Module, "this project's parts"),
      ...plain(["projects"], K.Module, "other projects' parts"),
      ...plain(["lib.parts"], K.Module, "global part library"),
      ...plain(["lib.design"], K.Module, "global design tokens"),
      ...plain(["build123d", "math", "typing"], K.Module),
      ...plain(["lib.params"], K.Module, "Range (slider bounds)"),
    ];
  }
  // from parts.|
  if (/^\s*from\s+parts\.$/.test(before)) {
    return (ownProj?.parts ?? []).map((p) => ({
      label: p.name,
      insert: p.name,
      kind: K.Module,
      detail: `part module — ${p.name}(${p.params})`,
      doc: p.doc,
    }));
  }
  // from parts.<mod> import |
  if ((m = before.match(/^\s*from\s+parts\.(\w+)\s+import\s+[\w\s,]*$/))) {
    const p = ownProj?.parts.find((x) => x.name === m![1]);
    return [{ label: m[1], insert: m[1], kind: K.Function, detail: p ? `${m[1]}(${p.params})` : "part", doc: p?.doc }];
  }
  // from projects.|
  if (/^\s*from\s+projects\.$/.test(before)) {
    return catalog.projects
      .filter((p) => p.name !== own)
      .map((p) => ({
        label: p.name,
        insert: p.name,
        kind: K.Module,
        detail: `${p.parts.length} part${p.parts.length === 1 ? "" : "s"}`,
      }));
  }
  // from projects.<p>.|
  if ((m = before.match(/^\s*from\s+projects\.(\w+)\.$/))) {
    return plain(["parts"], K.Module).concat(plain(["project"], K.Module, "its constants"));
  }
  // from projects.<p>.parts.|
  if ((m = before.match(/^\s*from\s+projects\.(\w+)\.parts\.$/))) {
    const proj = catalog.projects.find((p) => p.name === m![1]);
    return (proj?.parts ?? []).map((p) => ({
      label: p.name,
      insert: p.name,
      kind: K.Module,
      detail: `${p.name}(${p.params})`,
      doc: p.doc,
    }));
  }
  // from projects.<p>.parts.<mod> import |
  if ((m = before.match(/^\s*from\s+projects\.(\w+)\.parts\.(\w+)\s+import\s+[\w\s,]*$/))) {
    const proj = catalog.projects.find((p) => p.name === m![1]);
    const p = proj?.parts.find((x) => x.name === m![2]);
    return [{ label: m[2], insert: m[2], kind: K.Function, detail: p ? `${m[2]}(${p.params})` : "part", doc: p?.doc }];
  }
  // from lib.parts import |
  if (/^\s*from\s+lib\.parts\s+import\s+[\w\s,]*$/.test(before)) {
    return catalog.lib_parts.map((p) => ({
      label: p.name,
      insert: p.name,
      kind: K.Function,
      detail: p.signature,
      doc: p.doc,
    }));
  }
  // from project import |   (constants; also lib.design for global tokens)
  if (/^\s*from\s+project\s+import\s+[\w\s,]*$/.test(before)) {
    return (ownProj?.constants ?? []).map((c) => ({
      label: c,
      insert: c,
      kind: K.Constant,
      detail: "project constant",
    }));
  }
  if (/^\s*from\s+lib\.design\s+import\s+[\w\s,]*$/.test(before)) {
    return designTokens.map((c) => ({ label: c, insert: c, kind: K.Constant, detail: "design token" }));
  }
  // from build123d import |  — the full REAL API once introspection has loaded
  if (/^\s*from\s+build123d\s+import\s+[\w\s,*]*$/.test(before)) {
    const dsl = new Set(["show", "show_object", "require", "Range"]);
    const api = Object.keys(b3dDocs).filter((n) => !dsl.has(n));
    const names = api.length ? api.sort() : BUILD123D_NAMES;
    return names
      .map((n): Item => {
        const e = b3dDocs[n];
        return {
          label: n,
          insert: n,
          kind: e?.kind === "class" ? K.Class : e?.kind === "enum" ? K.Enum : K.Function,
          detail: e?.signature !== n ? e?.signature?.slice(0, 60) : undefined,
          doc: e ? b3dMarkdown(n, e) : undefined,
        };
      })
      .concat([{ label: "*", insert: "*", kind: K.Keyword, detail: "everything" }]);
  }
  // from lib.params import |
  if (/^\s*from\s+lib\.params\s+import\s+[\w\s,]*$/.test(before)) {
    return [{ label: "Range", insert: "Range", kind: K.Class, detail: "slider bounds for Annotated params" }];
  }
  void model;
  return null;
}

// --- the provider ---------------------------------------------------------------

monaco.languages.registerCompletionItemProvider("python", {
  triggerCharacters: [".", " "],
  async provideCompletionItems(model, position, context) {
    const word = model.getWordUntilPosition(position);
    const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
    const before = model.getLineContent(position.lineNumber).slice(0, word.startColumn - 1);
    const isImportLine = /^\s*(?:from|import)\s/.test(before) || /^\s*(?:from|import)?$/.test(before + word.word);

    // Space only triggers inside import lines (else every space pops the widget).
    if (
      context.triggerCharacter === " " &&
      !/^\s*from\s+[\w.]*\s+import\s*$/.test(before) &&
      !/^\s*from\s*$/.test(before)
    ) {
      return { suggestions: [] };
    }

    await refreshCatalog(); // TTL-cached; instant when warm

    let items: Item[] | null = null;
    if (isImportLine) items = importContext(before, model);
    if (items === null) {
      if (/\bAxis\.$/.test(before)) items = AXIS_MEMBERS;
      else if (/\bPlane\.$/.test(before)) items = PLANE_MEMBERS;
      else if (before.trimEnd().endsWith(".")) items = METHODS;
      else {
        const s = useStore.getState();
        const doc = s.docs.find((d) => d.id === s.activeDocId);
        const own = doc?.origin?.project ?? null;
        const ownProj = catalog.projects.find((p) => p.name === own);
        // Curated snippets first (hand-tuned inserts, real docs attached), then
        // the REST of the real API auto-generated from introspected signatures.
        const curated = new Set(TOP_LEVEL.map((t) => t.label));
        const apiExtras: Item[] = Object.entries(b3dDocs)
          .filter(([n]) => !curated.has(n))
          .map(([n, e]) => {
            const gen = b3dSnippet(n, e);
            return {
              label: n,
              insert: gen.insert,
              kind: e.kind === "class" ? K.Class : e.kind === "enum" ? K.Enum : K.Function,
              detail: "build123d",
              doc: b3dMarkdown(n, e),
              snippet: gen.snippet,
              sortText: "6" + n,
            };
          });
        items = [
          ...TOP_LEVEL.map((t) => ({
            ...t,
            doc: t.doc ?? (b3dDocs[t.label] ? b3dMarkdown(t.label, b3dDocs[t.label]) : undefined),
          })),
          ...apiExtras,
          ...partCompletions(model),
          ...(ownProj?.constants ?? []).map((t) => ({
            label: t,
            insert: t,
            kind: K.Constant,
            detail: "project constant",
            sortText: "1" + t,
          })),
          ...designTokens.map((t) => ({
            label: t,
            insert: t,
            kind: K.Constant,
            detail: "design token",
            sortText: "4" + t,
          })),
        ];
      }
    }

    return {
      suggestions: items.map((it) => ({
        label: it.label,
        kind: it.kind,
        insertText: it.insert,
        detail: it.detail,
        documentation: it.doc ? { value: it.doc } : undefined,
        range,
        sortText: it.sortText,
        insertTextRules: it.snippet ? SNIPPET : undefined,
        additionalTextEdits: it.extraEdits,
      })),
    };
  },
});

// --- hover docs -----------------------------------------------------------------
// Hover any known name → real signature + docstring (build123d/DSL), or a part's
// signature + doc, or what a constant is and where it lives.

monaco.languages.registerHoverProvider("python", {
  provideHover(model, position) {
    const w = model.getWordAtPosition(position);
    if (!w) return null;
    const name = w.word;
    const range = new monaco.Range(position.lineNumber, w.startColumn, position.lineNumber, w.endColumn);
    const md = (value: string) => ({ range, contents: [{ value }] });

    const e = b3dDocs[name];
    if (e) return md(b3dMarkdown(name, e));
    for (const p of catalog.projects) {
      const part = p.parts.find((x) => x.name === name);
      if (part) return md(`\`\`\`python\n${name}(${part.params})\n\`\`\`\n\n**part** · ${p.name}\n\n${part.doc}`);
    }
    const lp = catalog.lib_parts.find((x) => x.name === name);
    if (lp) return md(`\`\`\`python\n${lp.signature}\n\`\`\`\n\n**part** · library\n\n${lp.doc}`);
    if (designTokens.includes(name)) return md(`**${name}** — design token (\`lib.design\`)`);
    for (const p of catalog.projects) {
      if (p.constants.includes(name)) return md(`**${name}** — project constant (\`${p.name}/project.py\`)`);
    }
    return null;
  },
});

// --- parameter hints --------------------------------------------------------------
// Inside `Box(|` show the REAL parameter list with the active one highlighted —
// so "is it height or length first" is answered as you type. Works for
// build123d, the DSL, and parts (own/cross-project/library).

/** The innermost enclosing call at the cursor: its callee name + which argument
 *  the cursor is in (top-level comma count). Scans back across a few lines. */
function findEnclosingCall(
  model: monaco.editor.ITextModel,
  position: monaco.Position,
): { name: string; argIndex: number } | null {
  const startLine = Math.max(1, position.lineNumber - 3);
  let text = "";
  for (let l = startLine; l < position.lineNumber; l++) text += model.getLineContent(l) + "\n";
  text += model.getLineContent(position.lineNumber).slice(0, position.column - 1);
  let depth = 0;
  let commas = 0;
  for (let i = text.length - 1; i >= 0; i--) {
    const c = text[i];
    if (c === ")" || c === "]" || c === "}") depth++;
    else if (c === "(") {
      if (depth === 0) {
        const m = /([A-Za-z_]\w*)\s*$/.exec(text.slice(0, i));
        return m ? { name: m[1], argIndex: commas } : null;
      }
      depth--;
    } else if (c === "[" || c === "{") {
      if (depth > 0) depth--;
      else return null;
    } else if (c === "," && depth === 0) commas++;
  }
  return null;
}

/** Where a parameter name sits inside the signature label (word-boundary match),
 *  so Monaco highlights the right token even when the name recurs in a type. */
function paramSpan(sig: string, name: string): [number, number] | string {
  const m = new RegExp(`[(,]\\s*(${name})\\s*[:=,)]`).exec(sig);
  return m && m.index >= 0 ? [m.index + m[0].indexOf(name), m.index + m[0].indexOf(name) + name.length] : name;
}

function signatureFor(name: string): { label: string; doc: string; params: B3dParam[] } | null {
  const e = b3dDocs[name];
  if (e && e.kind !== "value" && e.kind !== "enum") {
    return { label: e.signature, doc: e.doc, params: e.params ?? [] };
  }
  const parse = (paramStr: string): B3dParam[] =>
    paramStr
      .split(",")
      .map((s) => s.split(/[:=]/)[0].trim())
      .filter(Boolean)
      .map((n) => ({ name: n }));
  for (const p of catalog.projects) {
    const part = p.parts.find((x) => x.name === name);
    if (part) return { label: `${name}(${part.params})`, doc: part.doc, params: parse(part.params) };
  }
  const lp = catalog.lib_parts.find((x) => x.name === name);
  if (lp) {
    const inner = lp.signature.slice(lp.signature.indexOf("(") + 1, lp.signature.lastIndexOf(")"));
    return { label: lp.signature, doc: lp.doc, params: parse(inner) };
  }
  return null;
}

monaco.languages.registerSignatureHelpProvider("python", {
  signatureHelpTriggerCharacters: ["(", ","],
  signatureHelpRetriggerCharacters: [","],
  provideSignatureHelp(model, position) {
    const call = findEnclosingCall(model, position);
    if (!call) return null;
    const info = signatureFor(call.name);
    if (!info) return null;
    return {
      value: {
        signatures: [
          {
            label: info.label,
            documentation: info.doc ? { value: info.doc.slice(0, 500) } : undefined,
            parameters: info.params.map((p) => ({
              label: paramSpan(info.label, p.name),
              documentation: p.default !== undefined ? { value: `default: \`${p.default}\`` } : undefined,
            })),
          },
        ],
        activeSignature: 0,
        activeParameter: Math.min(call.argIndex, Math.max(info.params.length - 1, 0)),
      },
      dispose() {
        /* nothing to release */
      },
    };
  },
});
