// #fmodal (OpenBot core.js pickFace): shapes in the chosen colour, then colours; the preview on top (#pface) plays
// the mood cycle after each pick. Done, Enter, Escape or the backdrop resolve with the draft (dialogs/ask.ts).
import { useEffect, useState } from "react";
import { endPickCycle, playPickCycle } from "../components/faceAnim";
import { Face } from "../components/Face";
import { FACE_COLORS, FACE_SHAPES } from "../lib/face";
import { settlePick, updatePick, useFacePick } from "./ask";

export function FacePicker() {
  const req = useFacePick();
  const [picks, setPicks] = useState(0);  // bumped per pick; the cycle starts once the new preview is committed
  useEffect(() => {
    if (!req) return;
    const key = (e: KeyboardEvent) => { if (e.key === "Escape" || e.key === "Enter") { e.stopPropagation(); e.preventDefault(); settlePick(); } };
    document.addEventListener("keydown", key, true);
    return () => { document.removeEventListener("keydown", key, true); endPickCycle(); };
  }, [req?.id]);
  useEffect(() => { if (picks) void playPickCycle(); }, [picks]);

  const draft = req?.draft;
  const pickShape = (shape: string) => { updatePick({ shape }); setPicks((n) => n + 1); };
  const pickColor = (color: string) => { updatePick({ color }); setPicks((n) => n + 1); };
  return (
    <div id="fmodal" className={`modal${req ? " show" : ""}`} style={{ zIndex: 50 }} role="dialog" aria-modal="true" aria-label="Edit avatar"
      onClick={(e) => { if (e.target === e.currentTarget) settlePick(); }}>
      <div className="box">
        <span className="pface" id="pfacew">{draft ? <Face b={{ face: draft }} svgId="pface" /> : null}</span>
        <div className="frow" id="fshapes">
          {draft ? Object.keys(FACE_SHAPES).map((k) => (
            <button key={k} data-shape={k} aria-pressed={k === draft.shape} title={k} onClick={() => pickShape(k)}>
              <Face b={{ face: { shape: k, color: draft.color } }} />
            </button>
          )) : null}
        </div>
        <div className="frow" id="fcolors">
          {draft ? FACE_COLORS.map((c) => (
            <button key={c} data-color={c} aria-pressed={c === draft.color} style={{ background: c }} onClick={() => pickColor(c)} />
          )) : null}
        </div>
        <div className="row"><button id="fdone" className="primary" onClick={settlePick}>Done</button></div>
      </div>
    </div>
  );
}
