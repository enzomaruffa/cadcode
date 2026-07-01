import { useEffect } from "react";

interface Section {
  title: string;
  blurb?: string;
  code: string;
}

// A beginner-friendly build123d cheat sheet, right in the app.
const SECTIONS: Section[] = [
  {
    title: "The idea",
    blurb: "Your model is a Python script. Build a part inside a BuildPart block, then show() it.",
    code: `with BuildPart() as plate:
    Box(80, 50, 12)          # length, width, height (mm)

show(plate.part, name="plate", color="#c9a06a")`,
  },
  {
    title: "Primitives",
    blurb: "Add these inside a `with BuildPart() as p:` block.",
    code: `Box(length, width, height)
Cylinder(radius, height)
Sphere(radius)
Cone(bottom_radius, top_radius, height)`,
  },
  {
    title: "Place & repeat",
    blurb: "Locations put copies at points; Hole subtracts by default.",
    code: `with Locations((-30, -15), (30, -15), (30, 15), (-30, 15)):
    Hole(radius=1.7)     # a hole at each spot

Pos(10, 0, 0) * Box(5, 5, 5)   # move something`,
  },
  {
    title: "Round & bevel edges",
    code: `fillet(p.edges().filter_by(Axis.Z), radius=2)          # round vertical edges
chamfer(p.faces().sort_by(Axis.Z)[-1].edges(), length=1)  # bevel the top`,
  },
  {
    title: "Selecting geometry (the tricky bit)",
    blurb: "Tip: click a face/edge in the 3D view — cadcode writes the selector for you.",
    code: `p.faces()                     # all faces
p.faces().sort_by(Axis.Z)[-1] # the topmost face
p.faces().filter_by(Plane.XY) # only horizontal faces
p.edges().filter_by(Axis.Z)   # vertical edges`,
  },
  {
    title: "cadcode extras",
    blurb: "Sliders, specs, and shared parts/tokens.",
    code: `SIZE: Annotated[float, Range(5, 80)] = 20   # a slider param
require(plate.part.volume > 1000, "solid")  # a spec the agent keeps green
from lib.design import WALL, FILLET, M3_TAP_D
from lib.parts import m3_boss, standoff`,
  },
];

export function HelpModal({ onClose }: { onClose: () => void }) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  return (
    <div className="modal-backdrop" onClick={onClose}>
      <div className="modal modal-help" onClick={(e) => e.stopPropagation()}>
        <div className="modal-head">
          <span className="modal-title">build123d reference</span>
          <button className="modal-close" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="modal-body">
          <p className="help-intro">
            New to 3D modelling? Start here, or just tell the <strong>agent</strong> what you want in plain English.
          </p>
          {SECTIONS.map((s) => (
            <div className="help-section" key={s.title}>
              <div className="help-title">{s.title}</div>
              {s.blurb && <div className="help-blurb">{s.blurb}</div>}
              <pre className="help-code">{s.code}</pre>
            </div>
          ))}
          <div className="help-links">
            <a href="https://build123d.readthedocs.io/en/latest/" target="_blank" rel="noopener noreferrer">
              Full build123d docs ↗
            </a>
            <a
              href="https://build123d.readthedocs.io/en/latest/cheat_sheet.html"
              target="_blank"
              rel="noopener noreferrer"
            >
              Official cheat sheet ↗
            </a>
          </div>
        </div>
      </div>
    </div>
  );
}
