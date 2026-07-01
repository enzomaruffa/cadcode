import * as monaco from "monaco-editor";
import { HTTP_URL } from "../config";

// Lightweight build123d autocomplete for the Python editor. Not a full language
// server — a curated list of primitives, operations, selectors, enums, cadcode
// helpers, and the live design tokens. After a "." we offer selector methods;
// otherwise top-level names. Monaco filters by the typed prefix.

const K = monaco.languages.CompletionItemKind;

interface Item {
  label: string;
  insert: string;
  kind: monaco.languages.CompletionItemKind;
  detail?: string;
  snippet?: boolean;
}

// Top-level: primitives + operations (algebra mode) + placement + helpers.
const TOP_LEVEL: Item[] = [
  // primitives (centered at origin)
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
  // 2D
  { label: "Rectangle", insert: "Rectangle(${1:width}, ${2:height})", kind: K.Function, snippet: true },
  { label: "Circle", insert: "Circle(${1:radius})", kind: K.Function, snippet: true },
  { label: "RegularPolygon", insert: "RegularPolygon(${1:radius}, ${2:side_count})", kind: K.Function, snippet: true },
  { label: "Text", insert: 'Text("${1:hi}", ${2:font_size})', kind: K.Function, snippet: true },
  // operations
  {
    label: "fillet",
    insert: "fillet(${1:objects}, radius=${2:2})",
    kind: K.Function,
    detail: "round edges",
    snippet: true,
  },
  {
    label: "chamfer",
    insert: "chamfer(${1:objects}, length=${2:1})",
    kind: K.Function,
    detail: "bevel edges",
    snippet: true,
  },
  { label: "extrude", insert: "extrude(${1:sketch}, amount=${2:5})", kind: K.Function, snippet: true },
  { label: "revolve", insert: "revolve(${1:sketch})", kind: K.Function, snippet: true },
  { label: "loft", insert: "loft([${1:sketches}])", kind: K.Function, snippet: true },
  { label: "sweep", insert: "sweep(${1:sketch}, path=${2:path})", kind: K.Function, snippet: true },
  { label: "offset", insert: "offset(${1:obj}, amount=${2:1})", kind: K.Function, snippet: true },
  { label: "mirror", insert: "mirror(${1:obj}, about=Plane.YZ)", kind: K.Function, snippet: true },
  { label: "scale", insert: "scale(${1:obj}, by=${2:2})", kind: K.Function, snippet: true },
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
  // enums / axes
  { label: "Axis", insert: "Axis", kind: K.Enum, detail: "Axis.X / Y / Z" },
  { label: "Plane", insert: "Plane", kind: K.Enum, detail: "Plane.XY / XZ / YZ" },
  { label: "Mode", insert: "Mode", kind: K.Enum, detail: "ADD / SUBTRACT / INTERSECT" },
  { label: "Align", insert: "Align", kind: K.Enum, detail: "MIN / CENTER / MAX" },
  { label: "Kind", insert: "Kind", kind: K.Enum },
  // cadcode helpers
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

// After a ".": selector + query methods.
const METHODS: Item[] = [
  { label: "faces", insert: "faces()", kind: K.Method },
  { label: "edges", insert: "edges()", kind: K.Method },
  { label: "vertices", insert: "vertices()", kind: K.Method },
  { label: "solids", insert: "solids()", kind: K.Method },
  { label: "wires", insert: "wires()", kind: K.Method },
  { label: "sort_by", insert: "sort_by(Axis.${1:Z})", kind: K.Method, snippet: true },
  { label: "filter_by", insert: "filter_by(Axis.${1:Z})", kind: K.Method, snippet: true },
  { label: "group_by", insert: "group_by(Axis.${1:Z})", kind: K.Method, snippet: true },
  { label: "bounding_box", insert: "bounding_box()", kind: K.Method },
  { label: "center", insert: "center()", kind: K.Method },
  { label: "edges", insert: "edges()", kind: K.Method },
];

const AXIS_MEMBERS: Item[] = ["X", "Y", "Z"].map((a) => ({ label: a, insert: a, kind: K.EnumMember }));
const PLANE_MEMBERS: Item[] = ["XY", "XZ", "YZ", "front", "top", "right"].map((p) => ({
  label: p,
  insert: p,
  kind: K.EnumMember,
}));

let designTokens: string[] = [];
fetch(`${HTTP_URL}/design`)
  .then((r) => r.json())
  .then((d: { tokens?: { name: string }[] }) => {
    designTokens = (d.tokens ?? []).map((t) => t.name);
  })
  .catch(() => void 0);

monaco.languages.registerCompletionItemProvider("python", {
  triggerCharacters: ["."],
  provideCompletionItems(model, position) {
    const word = model.getWordUntilPosition(position);
    const range = new monaco.Range(position.lineNumber, word.startColumn, position.lineNumber, word.endColumn);
    const before = model.getLineContent(position.lineNumber).slice(0, word.startColumn - 1);

    let items: Item[];
    if (/\bAxis\.$/.test(before)) items = AXIS_MEMBERS;
    else if (/\bPlane\.$/.test(before)) items = PLANE_MEMBERS;
    else if (before.trimEnd().endsWith(".")) items = METHODS;
    else
      items = [
        ...TOP_LEVEL,
        ...designTokens.map((t) => ({ label: t, insert: t, kind: K.Constant, detail: "design token" })),
      ];

    const suggestions = items.map((it) => ({
      label: it.label,
      kind: it.kind,
      insertText: it.insert,
      detail: it.detail,
      range,
      insertTextRules: it.snippet ? monaco.languages.CompletionItemInsertTextRule.InsertAsSnippet : undefined,
    }));
    return { suggestions };
  },
});
