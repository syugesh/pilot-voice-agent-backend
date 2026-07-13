/**
 * PPT Copilot — full view with:
 * - File upload (.pptx)
 * - Slide list with smooth navigation
 * - Voice "go to slide 42" support
 * - Agent activity panel
 */
import { useState, useRef, useEffect, useCallback } from "react";
import { useAppStore } from "../store/SessionStore";
import {
  XIcon, FolderUpIcon, PresentationIcon, PencilIcon, TrashIcon, CheckIcon,
  DotIcon, MicIcon, StopIcon, LockIcon, IconBadge,
} from "./Icons";

function useThemeColors() {
  const theme = useAppStore(s => s.theme);
  return theme === "dark"
    ? { bg:"#0F0F0F", surface:"#1A1A1A", border:"#2A2A2A", text1:"#F0F0F0", text2:"#AAA", text3:"#666", amber:"#F5A700", amberDark:"#D4900F", amberBg:"rgba(245,167,0,0.12)" }
    : { bg:"#F7F6F3", surface:"#FFFFFF", border:"#E5E2DA", text1:"#1A1A1A", text2:"#555", text3:"#888", amber:"#F5A700", amberDark:"#7C5E00", amberBg:"#FFF8E7" };
}

interface SlideShape { text:string; color:string|null; size:number; bold:boolean; italic?:boolean; underline?:boolean; font?:string|null; left?:number; top?:number; width?:number; height?:number; rotation?:number; align?:string; shape_id?:number; type?:"text"|"image"|string; }
interface Slide { index:number; title:string; notes:string; bg_color?:string; shapes?:SlideShape[]; image_url?:string; kind?:string|null; slide_width?:number; slide_height?:number; }

function looksDecorativeTitle(text = "", width = 90): boolean {
  const trimmed = text.trim();
  if (!trimmed) return true;
  if (/\b(?:https?:\/\/|www\.|[\w.-]+\.(?:com|org|net|io|ai|co|in))\b/i.test(trimmed)) return true;
  if (/^\d{1,2}(?:\s*\/\s*\d{1,2})?$/.test(trimmed)) return true;
  if (/^\d{1,2}\s+[A-Za-z]{3,9}\s+\d{4}$/.test(trimmed)) return true;
  if (/^[A-Za-z]{3,9}\s+\d{4}$/.test(trimmed)) return true; // "July 2026" — month name + year, no day
  return width < 8;
}

function inferSlideTitle(slide?: Slide, fallback = "Slide"): string {
  if (!slide) return fallback;
  // Slides from the GD template have their title resolved server-side by
  // the exact shape the generator wrote it into (see api/ppt.py's
  // _extract_sync) — trust it outright rather than re-guessing locally,
  // since the local heuristic below only has the (deliberately truncated)
  // shapes list to work from and can't be as precise.
  if (slide.kind && slide.title) return slide.title;
  if (!looksDecorativeTitle(slide.title)) return slide.title;
  const candidates = (slide.shapes || [])
    .filter(sh => !looksDecorativeTitle(sh.text, sh.width ?? 90) && !/^\d+\.\s{1,3}/.test(sh.text.trim()))
    .map(sh => ({
      text: sh.text.trim().split("\n")[0],
      score: (sh.size || 0) + (sh.bold ? 12 : 0) + ((sh.top ?? 50) < 75 ? 8 : 0) + (sh.text.length <= 55 ? 6 : 0),
    }))
    .filter(c => c.text);
  return candidates.length ? candidates.sort((a, b) => b.score - a.score)[0].text : fallback;
}

// ── Kind-aware edit form ──────────────────────────────────────────────────
// Slides generated from the GD template (team, table, comparison, ...) have
// a declarative field schema (see KIND_FIELD_SCHEMA in
// services/ppt_template_builder.py) instead of the old flat title/bullets
// model. Rather than one bespoke form layout per kind, this renders any
// schema generically from ~6 field types, composed recursively — a new kind
// added on the backend needs zero frontend changes.
interface KindField {
  key: string; type: string; label?: string;
  fields?: KindField[]; item_fields?: KindField[];
  max_length?: number; fixed_length?: number; max_cols?: number; textarea?: boolean;
}

function kindInputStyle(C: any) {
  return { width:"100%", boxSizing:"border-box" as const, padding:"0.5rem 0.65rem", borderRadius:6,
           border:`1.5px solid ${C.border}`, background:C.bg, color:C.text1, fontSize:"0.82rem",
           outline:"none", fontFamily:"inherit" };
}
function kindAddBtnStyle(C: any) {
  return { fontSize:"0.76rem", color:C.amber, background:"none", border:"none", cursor:"pointer", padding:"0.3rem 0" };
}
function kindRemoveBtnStyle(C: any) {
  return { width:22, height:22, borderRadius:"50%", border:"none", background:"rgba(239,68,68,0.15)",
           color:"#EF4444", cursor:"pointer", fontSize:"0.7rem", flexShrink:0,
           display:"flex", alignItems:"center", justifyContent:"center" };
}

function blankKindValue(field: KindField): any {
  if (field.type === "list_of_strings") return [];
  if (field.type === "list_of_objects") return [];
  if (field.type === "object") {
    const o: any = {};
    (field.fields || []).forEach(f => { o[f.key] = blankKindValue(f); });
    return o;
  }
  return "";
}

function KindFieldEditor({ field, value, onChange, C }: { field: KindField; value: any; onChange: (v: any) => void; C: any }) {
  const inputStyle = kindInputStyle(C);

  if (field.type === "text") {
    return <input value={value ?? ""} onChange={e => onChange(e.target.value)}
      placeholder={field.label} style={{ ...inputStyle, marginBottom:"0.9rem" }} />;
  }

  if (field.type === "textarea") {
    return <textarea value={value ?? ""} rows={3} onChange={e => onChange(e.target.value)}
      placeholder={field.label} style={{ ...inputStyle, resize:"vertical", marginBottom:"0.9rem" }} />;
  }

  if (field.type === "object") {
    const obj = value || {};
    return (
      <div style={{ border:`1px solid ${C.border}`, borderRadius:8, padding:"0.7rem", marginBottom:"0.9rem" }}>
        {(field.fields || []).map(sub => (
          <div key={sub.key} style={{ marginBottom:"0.3rem" }}>
            <label style={{ fontSize:"0.7rem", color:C.text3, display:"block", marginBottom:"0.2rem" }}>{sub.label}</label>
            <KindFieldEditor field={sub} value={obj[sub.key]} onChange={v => onChange({ ...obj, [sub.key]: v })} C={C} />
          </div>
        ))}
      </div>
    );
  }

  if (field.type === "list_of_strings") {
    const items: string[] = value || [];
    const canRemove = !field.fixed_length;
    const canAdd = !field.fixed_length && (!field.max_length || items.length < field.max_length);
    return (
      <div style={{ marginBottom:"0.9rem" }}>
        {items.map((item, i) => (
          <div key={i} style={{ display:"flex", gap:"0.4rem", marginBottom:"0.4rem", alignItems:"flex-start" }}>
            {field.textarea ? (
              <textarea value={item} rows={2}
                onChange={e => { const next = [...items]; next[i] = e.target.value; onChange(next); }}
                style={{ ...inputStyle, resize:"vertical" }} />
            ) : (
              <input value={item}
                onChange={e => { const next = [...items]; next[i] = e.target.value; onChange(next); }}
                style={inputStyle} />
            )}
            {canRemove && (
              <button onClick={() => onChange(items.filter((_, j) => j !== i))} style={{ ...kindRemoveBtnStyle(C), marginTop:2 }}><XIcon size={11}/></button>
            )}
          </div>
        ))}
        {canAdd && <button onClick={() => onChange([...items, ""])} style={kindAddBtnStyle(C)}>+ Add {field.label || "item"}</button>}
      </div>
    );
  }

  if (field.type === "list_of_objects") {
    const items: any[] = value || [];
    const canRemove = !field.fixed_length;
    const canAdd = !field.fixed_length && (!field.max_length || items.length < field.max_length);
    return (
      <div style={{ marginBottom:"0.9rem" }}>
        {items.map((item, i) => (
          <div key={i} style={{ border:`1px solid ${C.border}`, borderRadius:8, padding:"0.6rem",
                                marginBottom:"0.5rem", position:"relative" }}>
            {canRemove && (
              <button onClick={() => onChange(items.filter((_, j) => j !== i))}
                style={{ ...kindRemoveBtnStyle(C), position:"absolute", top:6, right:6 }}><XIcon size={11}/></button>
            )}
            {(field.item_fields || []).map(sub => (
              <div key={sub.key} style={{ marginBottom:"0.35rem", paddingRight: canRemove ? "1.8rem" : 0 }}>
                <label style={{ fontSize:"0.68rem", color:C.text3, display:"block", marginBottom:"0.15rem" }}>{sub.label}</label>
                <KindFieldEditor field={sub} value={item[sub.key]}
                  onChange={v => { const next = [...items]; next[i] = { ...item, [sub.key]: v }; onChange(next); }} C={C} />
              </div>
            ))}
          </div>
        ))}
        {canAdd && (
          <button onClick={() => {
            const blank: any = {};
            (field.item_fields || []).forEach(f => { blank[f.key] = blankKindValue(f); });
            onChange([...items, blank]);
          }} style={kindAddBtnStyle(C)}>+ Add {field.label || "item"}</button>
        )}
      </div>
    );
  }

  if (field.type === "table_grid") {
    const table = value || { headers: [], rows: [] };
    const headers: string[] = table.headers || [];
    const rows: string[][] = table.rows || [];
    const nCols = field.max_cols || headers.length;
    const setHeader = (c: number, v: string) => { const h = [...headers]; h[c] = v; onChange({ ...table, headers: h }); };
    const setCell = (r: number, c: number, v: string) => {
      const rr = rows.map(row => [...row]); rr[r][c] = v; onChange({ ...table, rows: rr });
    };
    return (
      <div style={{ marginBottom:"0.9rem", overflowX:"auto" }}>
        <table style={{ width:"100%", borderCollapse:"collapse", fontSize:"0.78rem" }}>
          <thead>
            <tr>
              {headers.map((h, c) => (
                <th key={c} style={{ padding:"0.2rem" }}>
                  <input value={h} onChange={e => setHeader(c, e.target.value)}
                    style={{ ...inputStyle, padding:"0.3rem 0.4rem", fontWeight:700 }} />
                </th>
              ))}
              <th style={{ width:26 }} />
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={r}>
                {row.map((cell, c) => (
                  <td key={c} style={{ padding:"0.2rem" }}>
                    <input value={cell} onChange={e => setCell(r, c, e.target.value)}
                      style={{ ...inputStyle, padding:"0.3rem 0.4rem" }} />
                  </td>
                ))}
                <td>
                  <button onClick={() => onChange({ ...table, rows: rows.filter((_, i) => i !== r) })}
                    style={kindRemoveBtnStyle(C)}><XIcon size={11}/></button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button onClick={() => onChange({ ...table, rows: [...rows, Array(nCols).fill("")] })}
          style={kindAddBtnStyle(C)}>+ Add Row</button>
      </div>
    );
  }

  return null;
}

/* ── WYSIWYG Slide Canvas ──────────────────────────────────────────────────
   Direct-manipulation editing over the fidelity PNG: each text shape becomes
   an absolutely-positioned overlay box (percent geometry from the backend)
   you can drag, resize via 8 handles, and double-click to edit inline. On
   drop/resize-end it PATCHes /slide/geometry (batched), and text edits PATCH
   /slide. Optimistic local updates; the refreshed thumbnail reconciles. */

type Handle = "nw"|"n"|"ne"|"e"|"se"|"s"|"sw"|"w";
const HANDLES: Handle[] = ["nw","n","ne","e","se","s","sw","w"];
const CANVAS_BLUE = "#1A73E8";

interface CanvasShape { shape_id:number; left:number; top:number; width:number; height:number;
                        rotation:number; text:string; color:string|null; size:number; bold:boolean;
                        italic:boolean; underline:boolean; font:string|null; align:string; type:string; }

const FONT_CHOICES = ["Arial", "Calibri", "Georgia", "Times New Roman", "Verdana", "Courier New"];

function SlideCanvas({ slide, sid, token, onDirty }:
  { slide: Slide; sid: string; token: string|null; onDirty: ()=>void }) {
  const C = useThemeColors();
  const boxRef = useRef<HTMLDivElement>(null);
  const imageFileRef = useRef<HTMLInputElement>(null);
  const textAreaRefs = useRef<Record<number, HTMLTextAreaElement | null>>({});
  // Local editable copy of the slide's shapes (only those with a shape_id are
  // canvas-editable — a slide's decorative/imageless bits without ids stay in
  // the background PNG).
  const [shapes, setShapes] = useState<CanvasShape[]>([]);
  const [selected, setSelected] = useState<number|null>(null);
  const [editing, setEditing] = useState<number|null>(null);
  const [replacingImage, setReplacingImage] = useState(false);
  const dragRef = useRef<any>(null);

  // Re-seed only on actual slide navigation. Do NOT key this on image_url:
  // every geometry/text edit triggers a debounced background refresh that
  // returns a new cache-busted image_url for the *same* slide, and resetting
  // shapes/selection/editing on that would blow away in-progress edits (and,
  // combined with the img remount below, flash the canvas blank/white).
  useEffect(() => {
    const next = (slide.shapes || [])
      .filter(s => s.shape_id != null)
      .map(s => ({
        shape_id: s.shape_id!, left: s.left ?? 0, top: s.top ?? 0,
        width: s.width ?? 20, height: s.height ?? 10, rotation: s.rotation ?? 0,
        text: s.text ?? "", color: s.color ?? null, size: s.size ?? 18,
        bold: !!s.bold, italic: !!s.italic, underline: !!s.underline, font: s.font ?? null,
        align: s.align || "left", type: s.type || "text",
      }));
    setShapes(next);
    setSelected(null);
    setEditing(null);
  }, [slide.index]);

  // Background PNG: swap the visible src only after the new image has
  // actually finished loading, so a refresh mid-edit never renders a blank
  // frame while the old <img> is torn down and the new one loads.
  const [displayedImageUrl, setDisplayedImageUrl] = useState(slide.image_url);
  useEffect(() => {
    if (!slide.image_url || slide.image_url === displayedImageUrl) return;
    const img = new Image();
    let cancelled = false;
    img.onload = () => { if (!cancelled) setDisplayedImageUrl(slide.image_url); };
    img.src = slide.image_url;
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [slide.image_url]);

  const patchGeometry = useCallback(async (moved: CanvasShape[]) => {
    try {
      const res = await fetch("/api/v1/ppt/slide/geometry", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({
          session_id: sid, slide_index: slide.index,
          shapes: moved.map(s => ({ shape_id: s.shape_id, left: s.left, top: s.top, width: s.width, height: s.height, rotation: s.rotation })),
        }),
      });
      if (res.ok) onDirty();
    } catch (e) { console.error("geometry patch failed", e); }
  }, [sid, token, slide.index, onDirty]);

  const patchText = useCallback(async (shape_id:number, text:string) => {
    try {
      const res = await fetch("/api/v1/ppt/slide/shape-text", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, slide_index: slide.index, shape_id, text }),
      });
      if (res.ok) onDirty();
    } catch (e) { console.error("text patch failed", e); }
  }, [sid, token, slide.index, onDirty]);

  const patchImage = useCallback(async (shape_id:number, file:File) => {
    setReplacingImage(true);
    try {
      const fd = new FormData();
      fd.append("session_id", sid);
      fd.append("slide_index", String(slide.index));
      fd.append("shape_id", String(shape_id));
      fd.append("file", file);
      const res = await fetch("/api/v1/ppt/slide/shape-image", {
        method: "PATCH",
        headers: { Authorization: `Bearer ${token}` },
        body: fd,
      });
      if (res.ok) onDirty();
    } catch (e) {
      console.error("image patch failed", e);
    } finally {
      setReplacingImage(false);
    }
  }, [sid, token, slide.index, onDirty]);

  const patchStyle = useCallback(async (shape_id:number, style: Partial<Pick<CanvasShape,"bold"|"italic"|"underline"|"font"|"size"|"color"|"align">>) => {
    setShapes(prev => prev.map(s => s.shape_id === shape_id ? { ...s, ...style } : s));
    try {
      const res = await fetch("/api/v1/ppt/slide/shape-style", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, slide_index: slide.index, shape_id, ...style }),
      });
      if (res.ok) onDirty();
    } catch (e) { console.error("style patch failed", e); }
  }, [sid, token, slide.index, onDirty]);

  const addTextBox = useCallback(async () => {
    try {
      const res = await fetch("/api/v1/ppt/slide/shape-add-text", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, slide_index: slide.index, text: "New text" }),
      }).then(r => r.json());
      if (res.shape_id != null) {
        setShapes(prev => [...prev, {
          shape_id: res.shape_id, left: 35, top: 40, width: 30, height: 12, rotation: 0,
          text: "New text", color: "#000000", size: 24, bold: false, italic: false,
          underline: false, font: null, align: "left", type: "text",
        }]);
        onDirty();
      }
    } catch (e) { console.error("add text box failed", e); }
  }, [sid, token, slide.index, onDirty]);

  const deleteSelectedShape = useCallback(async (shape_id:number) => {
    setShapes(prev => prev.filter(s => s.shape_id !== shape_id));
    setSelected(null);
    try {
      const res = await fetch("/api/v1/ppt/slide/shape", {
        method: "DELETE",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, slide_index: slide.index, shape_id }),
      });
      if (res.ok) onDirty();
    } catch (e) { console.error("delete shape failed", e); }
  }, [sid, token, slide.index, onDirty]);

  const reorderShape = useCallback(async (shape_id:number, direction:"front"|"back") => {
    try {
      const res = await fetch("/api/v1/ppt/slide/shape-zorder", {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, slide_index: slide.index, shape_id, direction }),
      });
      if (res.ok) onDirty();
    } catch (e) { console.error("z-order patch failed", e); }
  }, [sid, token, slide.index, onDirty]);

  const beginTextEdit = useCallback((shape_id:number) => {
    setSelected(shape_id);
    setEditing(shape_id);
    window.setTimeout(() => {
      const node = textAreaRefs.current[shape_id];
      if (!node) return;
      node.focus();
      const end = node.value.length;
      node.setSelectionRange(end, end);
    }, 0);
  }, []);

  // Pointer-based drag / resize. Deltas computed against the canvas box size so
  // percent stays aspect-correct regardless of on-screen scale.
  const onPointerDown = (e: React.PointerEvent, shape_id:number, mode:"move"|Handle) => {
    if (editing != null) return;
    e.preventDefault(); e.stopPropagation();
    setSelected(shape_id);
    const box = boxRef.current!.getBoundingClientRect();
    const s = shapes.find(x => x.shape_id === shape_id)!;
    dragRef.current = { shape_id, mode, startX: e.clientX, startY: e.clientY,
                        orig: { ...s }, boxW: box.width, boxH: box.height };
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  };

  const onPointerMove = (e: React.PointerEvent) => {
    const d = dragRef.current;
    if (!d) return;
    const dxPct = ((e.clientX - d.startX) / d.boxW) * 100;
    const dyPct = ((e.clientY - d.startY) / d.boxH) * 100;
    setShapes(prev => prev.map(s => {
      if (s.shape_id !== d.shape_id) return s;
      let { left, top, width, height } = d.orig;
      const m = d.mode as string;
      if (m === "move") { left += dxPct; top += dyPct; }
      else {
        if (m.includes("e")) width  = Math.max(3, d.orig.width  + dxPct);
        if (m.includes("s")) height = Math.max(3, d.orig.height + dyPct);
        if (m.includes("w")) { width = Math.max(3, d.orig.width - dxPct); left = d.orig.left + dxPct; }
        if (m.includes("n")) { height = Math.max(3, d.orig.height - dyPct); top = d.orig.top + dyPct; }
      }
      return { ...s, left: Math.max(0, left), top: Math.max(0, top), width, height };
    }));
  };

  const onPointerUp = (e: React.PointerEvent) => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d) return;
    (e.target as HTMLElement).releasePointerCapture?.(e.pointerId);
    const moved = shapes.find(s => s.shape_id === d.shape_id);
    if (!moved) return;
    const changed =
      Math.abs(moved.left - d.orig.left) > 0.05 ||
      Math.abs(moved.top - d.orig.top) > 0.05 ||
      Math.abs(moved.width - d.orig.width) > 0.05 ||
      Math.abs(moved.height - d.orig.height) > 0.05 ||
      Math.abs((moved.rotation || 0) - (d.orig.rotation || 0)) > 0.05;
    if (changed) patchGeometry([moved]);
  };

  const commitText = (shape_id:number, text:string) => {
    setEditing(null);
    setShapes(prev => prev.map(s => s.shape_id===shape_id ? { ...s, text } : s));
    const orig = (slide.shapes || []).find(s => s.shape_id === shape_id)?.text ?? "";
    if (text !== orig) patchText(shape_id, text);
  };

  const selectedShape = shapes.find(s => s.shape_id === selected) || null;

  return (
    <div ref={boxRef}
         onPointerMove={onPointerMove} onPointerUp={onPointerUp}
         onPointerDown={() => { setSelected(null); setEditing(null); }}
         style={{ position:"absolute", inset:0, cursor:"default" }}>
      {/* Fidelity PNG background — src only swaps once the new image has
          preloaded (see displayedImageUrl above), so this never unmounts
          mid-edit or flashes blank while a fresher render is still in flight. */}
      {displayedImageUrl && (
        <img src={displayedImageUrl} alt=""
             draggable={false}
             style={{ position:"absolute", inset:0, width:"100%", height:"100%",
                      objectFit:"contain", pointerEvents:"none", userSelect:"none" }}/>
      )}

      {/* Interactive shape overlay */}
      {shapes.map(s => {
        const isSel = selected === s.shape_id;
        const isEdit = editing === s.shape_id;
        return (
          <div key={s.shape_id}
               onPointerDown={e => onPointerDown(e, s.shape_id, "move")}
               onDoubleClick={e => { e.stopPropagation(); if (s.type !== "image") beginTextEdit(s.shape_id); }}
               style={{ position:"absolute",
                        left:`${s.left}%`, top:`${s.top}%`, width:`${s.width}%`, height:`${s.height}%`,
                        transform: s.rotation ? `rotate(${s.rotation}deg)` : undefined,
                        border: isSel ? `1.5px solid ${CANVAS_BLUE}` : "1.5px solid transparent",
                        background: "transparent",
                        cursor: isEdit ? "text" : "move", boxSizing:"border-box",
                        transition:"border-color 0.1s" }}>
            {isEdit ? (
              <textarea
                ref={node => { textAreaRefs.current[s.shape_id] = node; }}
                value={s.text}
                autoFocus
                onPointerDown={e=>e.stopPropagation()}
                onChange={e => {
                  const text = e.target.value;
                  setShapes(prev => prev.map(item => item.shape_id === s.shape_id ? { ...item, text } : item));
                }}
                onBlur={e => commitText(s.shape_id, e.target.value)}
                onKeyDown={e => {
                  if (e.key === "Escape") setEditing(null);
                  if ((e.metaKey || e.ctrlKey) && e.key === "Enter") commitText(s.shape_id, e.currentTarget.value);
                }}
                style={{ width:"100%", height:"100%", resize:"none", border:"none",
                         outline:"none",
                         // Opaque, matching the slide's own background — an editable
                         // field should fully replace the flattened PNG text underneath
                         // it, not overlay a translucent layer on top (which doubled
                         // the text and looked like a rendering glitch). Many slides
                         // inherit their background from the layout/master rather than
                         // setting an explicit fill, in which case the backend can't
                         // resolve one and falls back to a dark default — that default
                         // is unsafe here (dark text would vanish), so treat it as
                         // "unknown" and use white, which matches the vast majority of
                         // real decks.
                         background: (slide.bg_color && slide.bg_color.toLowerCase() !== "#111111")
                           ? slide.bg_color : "#FFFFFF",
                         color:s.color || "#111", fontWeight:s.bold ? 700 : 400,
                         fontStyle:s.italic ? "italic" : "normal",
                         textDecoration:s.underline ? "underline" : "none",
                         fontSize:`${Math.max(10, Math.min(34, s.size * 0.55))}px`,
                         lineHeight:1.2, padding:"4px 6px", boxSizing:"border-box",
                         fontFamily:s.font || "inherit", textAlign:(s.align as any)||"left",
                         caretColor:CANVAS_BLUE, overflow:"hidden",
                         boxShadow:"none" }}/>
            ) : s.type === "image" && isSel ? (
              <div style={{ position:"absolute", inset:0, background:"rgba(26,115,232,0.08)",
                            display:"flex", alignItems:"center", justifyContent:"center",
                            color:"#fff", fontSize:"0.62rem", fontWeight:700,
                            textShadow:"0 1px 4px rgba(0,0,0,0.7)", pointerEvents:"none" }}>
                IMAGE
              </div>
            ) : null}
            {/* Resize handles (only when selected, not editing) */}
            {isSel && !isEdit && HANDLES.map(h => (
              <div key={h}
                   onPointerDown={e => onPointerDown(e, s.shape_id, h)}
                   style={{ position:"absolute", width:9, height:9, background:"#fff",
                            border:`1.5px solid ${CANVAS_BLUE}`, borderRadius:1, zIndex:5,
                            cursor:`${h}-resize`,
                            ...handlePos(h) }}/>
            ))}
          </div>
        );
      })}
      {selectedShape && selectedShape.type !== "image" && (
        <FormatToolbar shape={selectedShape}
          onStyle={style => patchStyle(selectedShape.shape_id, style)}
          onEditText={() => beginTextEdit(selectedShape.shape_id)}
          onDelete={() => deleteSelectedShape(selectedShape.shape_id)}
          onFront={() => reorderShape(selectedShape.shape_id, "front")}
          onBack={() => reorderShape(selectedShape.shape_id, "back")}
          C={C} />
      )}
      {selectedShape?.type === "image" && (
        <div style={{ position:"absolute", left:10, bottom:10, zIndex:20,
                      display:"flex", alignItems:"center", gap:"0.4rem",
                      padding:"0.35rem 0.45rem", borderRadius:6,
                      background:"rgba(15,15,15,0.82)", color:"#fff",
                      boxShadow:"0 4px 16px rgba(0,0,0,0.28)" }}
             onPointerDown={e => e.stopPropagation()}>
          <button onClick={() => imageFileRef.current?.click()}
            disabled={replacingImage}
            style={{ border:"none", borderRadius:5, background:C.amber,
                     color:"#fff", fontSize:"0.68rem", fontWeight:700,
                     padding:"0.32rem 0.55rem", cursor:replacingImage ? "default" : "pointer" }}>
            {replacingImage ? "Replacing..." : "Replace image"}
          </button>
          <button onClick={() => reorderShape(selectedShape.shape_id, "front")}
            style={toolbarIconBtnStyle()} title="Bring to front">⬆</button>
          <button onClick={() => reorderShape(selectedShape.shape_id, "back")}
            style={toolbarIconBtnStyle()} title="Send to back">⬇</button>
          <button onClick={() => deleteSelectedShape(selectedShape.shape_id)}
            style={{ ...toolbarIconBtnStyle(), color:"#F87171" }} title="Delete">
            <TrashIcon size={12}/>
          </button>
          <span style={{ fontSize:"0.62rem", color:"rgba(255,255,255,0.72)" }}>Drag corners to resize</span>
        </div>
      )}
      <input ref={imageFileRef} type="file" accept="image/*" style={{ display:"none" }}
        onChange={e => {
          const file = e.target.files?.[0];
          if (file && selectedShape?.type === "image") patchImage(selectedShape.shape_id, file);
          e.currentTarget.value = "";
        }} />

      {/* Add text box — floating action, always available in canvas mode */}
      <button onClick={e => { e.stopPropagation(); addTextBox(); }}
        onPointerDown={e => e.stopPropagation()}
        title="Add text box"
        style={{ position:"absolute", right:10, top:10, zIndex:20,
                 border:"none", borderRadius:6, background:C.amber, color:"#fff",
                 fontSize:"0.72rem", fontWeight:700, padding:"0.4rem 0.65rem", cursor:"pointer",
                 boxShadow:"0 4px 16px rgba(0,0,0,0.28)", display:"flex", alignItems:"center", gap:"0.3rem" }}>
        + Text box
      </button>
    </div>
  );
}

function toolbarIconBtnStyle(active = false): React.CSSProperties {
  return { border:"none", borderRadius:5, background: active ? "rgba(245,167,0,0.35)" : "rgba(255,255,255,0.1)",
           color:"#fff", fontSize:"0.72rem", fontWeight:700, width:24, height:24,
           display:"flex", alignItems:"center", justifyContent:"center", cursor:"pointer" };
}

type StylePatch = Partial<Pick<CanvasShape,"bold"|"italic"|"underline"|"font"|"size"|"color"|"align">>;

/* Google-Slides-style floating format toolbar shown under the selected text
   shape: bold/italic/underline, font family + size, color, alignment, plus
   edit/layer/delete actions. Every control PATCHes immediately (no "Save"
   step) so it matches direct-manipulation editors rather than a form. */
function FormatToolbar({ shape, onStyle, onEditText, onDelete, onFront, onBack, C }:
  { shape: CanvasShape; onStyle: (s: StylePatch) => void; onEditText: () => void;
    onDelete: () => void; onFront: () => void; onBack: () => void; C: any }) {
  return (
    <div style={{ position:"absolute", left:10, bottom:10, zIndex:20,
                  display:"flex", alignItems:"center", gap:"0.35rem", flexWrap:"wrap",
                  padding:"0.4rem 0.5rem", borderRadius:8, maxWidth:"calc(100% - 20px)",
                  background:"rgba(15,15,15,0.88)", color:"#fff",
                  boxShadow:"0 4px 16px rgba(0,0,0,0.28)" }}
         onPointerDown={e => e.stopPropagation()}>
      <button onClick={onEditText}
        style={{ border:"none", borderRadius:5, background:C.amber, color:"#fff",
                 fontSize:"0.68rem", fontWeight:700, padding:"0.28rem 0.5rem", cursor:"pointer" }}>
        Edit text
      </button>

      <div style={{ width:1, height:18, background:"rgba(255,255,255,0.2)" }}/>

      <select value={shape.font || ""} onChange={e => onStyle({ font: e.target.value || null })}
        style={{ background:"#222", color:"#fff", border:"1px solid #444", borderRadius:4,
                 fontSize:"0.68rem", padding:"0.2rem 0.3rem", maxWidth:110 }}>
        <option value="">Default font</option>
        {FONT_CHOICES.map(f => <option key={f} value={f}>{f}</option>)}
      </select>

      <input type="number" min={6} max={120} value={Math.round(shape.size)}
        onChange={e => onStyle({ size: Number(e.target.value) || shape.size })}
        style={{ width:42, background:"#222", color:"#fff", border:"1px solid #444",
                 borderRadius:4, fontSize:"0.68rem", padding:"0.2rem 0.25rem" }}/>

      <div style={{ width:1, height:18, background:"rgba(255,255,255,0.2)" }}/>

      <button onClick={() => onStyle({ bold: !shape.bold })} style={toolbarIconBtnStyle(shape.bold)} title="Bold">
        <b>B</b>
      </button>
      <button onClick={() => onStyle({ italic: !shape.italic })} style={toolbarIconBtnStyle(shape.italic)} title="Italic">
        <i>I</i>
      </button>
      <button onClick={() => onStyle({ underline: !shape.underline })} style={toolbarIconBtnStyle(shape.underline)} title="Underline">
        <u>U</u>
      </button>

      <input type="color" value={shape.color || "#000000"}
        onChange={e => onStyle({ color: e.target.value })}
        title="Text color"
        style={{ width:24, height:24, padding:0, border:"1px solid #444", borderRadius:5,
                 background:"none", cursor:"pointer" }}/>

      <div style={{ width:1, height:18, background:"rgba(255,255,255,0.2)" }}/>

      {(["left","center","right","justify"] as const).map(a => (
        <button key={a} onClick={() => onStyle({ align: a })} style={toolbarIconBtnStyle(shape.align === a)}
          title={`Align ${a}`}>
          {a === "left" ? "⯇" : a === "center" ? "≡" : a === "right" ? "⯈" : "☰"}
        </button>
      ))}

      <div style={{ width:1, height:18, background:"rgba(255,255,255,0.2)" }}/>

      <button onClick={onFront} style={toolbarIconBtnStyle()} title="Bring to front">⬆</button>
      <button onClick={onBack} style={toolbarIconBtnStyle()} title="Send to back">⬇</button>
      <button onClick={onDelete} style={{ ...toolbarIconBtnStyle(), color:"#F87171" }} title="Delete shape">
        <TrashIcon size={12}/>
      </button>
    </div>
  );
}

function handlePos(h: Handle): React.CSSProperties {
  const c = -5; // center a 9px handle on the edge
  const map: Record<Handle, React.CSSProperties> = {
    nw:{left:c,top:c}, n:{left:"calc(50% - 4.5px)",top:c}, ne:{right:c,top:c},
    e:{right:c,top:"calc(50% - 4.5px)"}, se:{right:c,bottom:c},
    s:{left:"calc(50% - 4.5px)",bottom:c}, sw:{left:c,bottom:c}, w:{left:c,top:"calc(50% - 4.5px)"},
  };
  return map[h];
}

function mergeSlidesKeepingImages(previous: Slide[], incoming: Slide[]): Slide[] {
  return incoming.map((slide, i) => {
    if (slide.image_url) return slide;
    const prior = previous.find(p => p.index === slide.index) || previous[i];
    return prior?.image_url ? { ...slide, image_url: prior.image_url } : slide;
  });
}

// Stable per-tab id for uploads made before a live voice session exists —
// avoids every such upload colliding into one shared "default" backend slot.
function getAnonPptSid(): string {
  let s = sessionStorage.getItem("pilot_ppt_anon_sid");
  if (!s) {
    s = `anon-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
    sessionStorage.setItem("pilot_ppt_anon_sid", s);
  }
  return s;
}

export function PPTCopilotView({ sessionId, isListening, agentStatus }:
  { sessionId:string|null; isListening:boolean; agentStatus:string; onToggleMic:()=>void }) {
  const store  = useAppStore();
  const C      = useThemeColors();
  const token  = store.token;
  // Restore from store so slides survive tab switches
  const [slides,     setSlides]     = useState<Slide[]>((store.pptSlides as Slide[]) || []);
  const [current,    setCurrent]    = useState(0);
  const [canvasMode, setCanvasMode] = useState(true);   // WYSIWYG canvas (default editor) vs static preview
  const [uploading,  setUploading]  = useState(false);
  const [fileName,   setFileName]   = useState(store.pptFileName || "");
  const [agentLog,   setAgentLog]   = useState<string[]>([]);
  const [thumbStart, setThumbStart] = useState(0);
  const THUMB_COUNT = 6;
  const fileRef = useRef<HTMLInputElement>(null);

  // Keep thumbnail window centred on the active slide
  useEffect(() => {
    if (current < thumbStart) setThumbStart(current);
    else if (current >= thumbStart + THUMB_COUNT) setThumbStart(current - THUMB_COUNT + 1);
  }, [current]);

  // Keyboard navigation — left/right arrows
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      if (e.key === "ArrowRight" || e.key === "ArrowDown")
        setCurrent(c => Math.min(c + 1, Math.max(slides.length - 1, 0)));
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp")
        setCurrent(c => Math.max(c - 1, 0));
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [slides.length]);

  // Show "Access Denied" modal when policy blocks ppt_delete_slide for non-admins
  useEffect(() => {
    const handler = (e: CustomEvent) => {
      if (e.detail?.tool === "ppt_delete_slide") setDeleteModal("denied");
    };
    window.addEventListener("tool_blocked" as any, handler);
    return () => window.removeEventListener("tool_blocked" as any, handler);
  }, []);

  async function uploadFile(file: File) {
    if (!file.name.endsWith(".pptx")) { alert("Please upload a .pptx file"); return; }
    setUploading(true);
    const name = file.name;
    setFileName(name);
    store.setPptFileName(name);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const sid = sessionId || getAnonPptSid();
      const res = await fetch(`/api/v1/ppt/upload?session_id=${sid}`, {
        method:"POST", headers:{Authorization:`Bearer ${token}`}, body:fd
      }).then(r=>r.json());
      const sl: Slide[] = res.slides || [];
      setSlides(sl);
      store.setPptSlides(sl);
      setCurrent(0);
      setAgentLog(l => [...l, `✓ Loaded ${sl.length} slides from ${name}`]);
    } catch(e) {
      setAgentLog(l => [...l, "✗ Upload failed"]);
    } finally { setUploading(false); }
  }

  function removePresentation() {
    setSlides([]);
    setFileName("");
    setAgentLog([]);
    store.setPptSlides([]);
    store.setPptFileName("");
  }

  // Delete slide — admin only (Level 3 access)
  const [deleteModal,   setDeleteModal]   = useState<"hidden"|"denied"|"confirm">("hidden");

  // ── History state ──────────────────────────────────────────────────────────
  const [historyOpen,   setHistoryOpen]   = useState(false);
  const [historyList,   setHistoryList]   = useState<any[]>([]);
  const [historyLoading,setHistoryLoading]= useState(false);

  async function openHistory() {
    setHistoryOpen(true);
    setHistoryLoading(true);
    try {
      const res = await fetch("/api/v1/ppt/history", {
        headers: { Authorization: `Bearer ${token}` }
      }).then(r => r.json());
      setHistoryList(res.history || []);
    } catch { setHistoryList([]); }
    setHistoryLoading(false);
  }

  async function loadFromHistory(sid: string) {
    setHistoryOpen(false);
    setUploading(true);
    try {
      const res = await fetch(`/api/v1/ppt/history/load/${sid}`, {
        headers: { Authorization: `Bearer ${token}` }
      }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
      const sl: Slide[] = res.slides || [];
      setSlides(sl);
      store.setPptSlides(sl);
      store.setPptFileName(res.title);
      setFileName(res.title);
      setCurrent(0);
      setGeneratedSid(sid);
      setAgentLog(l => [...l, `✓ Loaded "${res.title}" from history`]);
    } catch(e: any) {
      setAgentLog(l => [...l, `✗ Failed to load: ${e.message}`]);
    }
    setUploading(false);
  }

  // ── Create PPT state ────────────────────────────────────────────────────────
  const [createModal,   setCreateModal]   = useState(false);
  const [genDesc,       setGenDesc]       = useState("");
  const [genCount,      setGenCount]      = useState(10);
  const [generating,    setGenerating]    = useState(false);
  const [genProgress,   setGenProgress]   = useState("");
  const [generatedSid,  setGeneratedSid]  = useState<string|null>(null); // has downloadable file
  const [micListening,  setMicListening]  = useState(false);
  const recognitionRef  = useRef<any>(null);

  // ── Add slide state ─────────────────────────────────────────────────────
  const [addSlideModal, setAddSlideModal] = useState(false);
  const [addSlideDesc,  setAddSlideDesc]  = useState("");
  const [addingSlide,   setAddingSlide]   = useState(false);
  const [addSlideError, setAddSlideError] = useState("");

  // ── Edit slide state ──────────────────────────────────────────────────
  const [editOpen,      setEditOpen]      = useState(false);
  const [savingEdit,    setSavingEdit]    = useState(false);
  const [editDraft,     setEditDraft]     = useState<{
    title: string;
    bullets: string[];
  }>({ title: "", bullets: [] });
  const [notesDraft,    setNotesDraft]    = useState("");
  // Present only for slides generated from the GD template (a known "kind");
  // absent (null) for uploaded/legacy slides, which use editDraft above
  // exactly as before this feature existed.
  const [kindSchema,    setKindSchema]    = useState<{ kind:string; fields:KindField[] } | null>(null);
  const [kindDraft,     setKindDraft]     = useState<any>(null);

  // Refetch slides from disk (picks up the re-rendered thumbnail after a
  // canvas geometry/text edit). Debounced so a rapid drag doesn't queue a
  // pile of full-deck LibreOffice renders — only the last change reconciles.
  const refreshTimer = useRef<number>(0);
  const refreshSlides = useCallback(() => {
    window.clearTimeout(refreshTimer.current);
    refreshTimer.current = window.setTimeout(() => {
      const sid = generatedSid || sessionId || getAnonPptSid();
      fetch(`/api/v1/ppt/slides/${sid}`, { headers: { Authorization: `Bearer ${token}` } })
        .then(r => r.json())
        .then(res => {
          if (res.slides) {
            setSlides(prev => {
              const merged = mergeSlidesKeepingImages(prev, res.slides);
              store.setPptSlides(merged);
              return merged;
            });
          }
        })
        .catch(console.error);
    }, 700);
  }, [generatedSid, sessionId, token]);

  // Listen for ppt_command events (navigation + voice-triggered delete + refresh)
  useEffect(() => {
    const handler = (e: CustomEvent) => {
      const { action, index, session_id } = e.detail;
      if (action === "goto" && index !== undefined) {
        setCurrent(Math.max(0, Math.min(index, Math.max(slides.length-1, 0))));
        setAgentLog(l => [...l.slice(-9), `Navigated to slide ${index+1}`]);
      } else if (action === "next")   setCurrent(c => Math.min(c+1, Math.max(slides.length-1, 0)));
      else if (action === "prev")     setCurrent(c => Math.max(c-1, 0));
      else if (action === "first")    setCurrent(0);
      else if (action === "last")     setCurrent(Math.max(slides.length-1, 0));
      else if (action === "delete")   setDeleteModal("confirm");
      else if (action === "refresh") {
        const sid = session_id || generatedSid || sessionId || getAnonPptSid();
        fetch(`/api/v1/ppt/slides/${sid}`, { headers: { Authorization: `Bearer ${token}` } })
          .then(r => r.json())
          .then(res => {
            if (res.slides) {
              setSlides(prev => {
                const merged = mergeSlidesKeepingImages(prev, res.slides);
                store.setPptSlides(merged);
                return merged;
              });
            }
            setAgentLog(l => [...l.slice(-9), "Slide updated by agent."]);
          }).catch(console.error);
      }
    };
    window.addEventListener("ppt_command" as any, handler);
    return () => window.removeEventListener("ppt_command" as any, handler);
  }, [slides.length, generatedSid, sessionId, token]);

  async function openEditModal() {
    const slide = slides[current];
    if (!slide) return;
    setNotesDraft(slide.notes || "");
    const sid = sessionId || getAnonPptSid();

    // Slides generated from the GD template carry a known "kind" — fetch
    // its field schema + current values and render the generic kind-aware
    // form. Uploaded/legacy slides (or a failed lookup) fall through to
    // the original title/bullets form, unchanged.
    try {
      const res = await fetch(`/api/v1/ppt/slide/${sid}/${current}/schema`, {
        headers: { Authorization: `Bearer ${token}` },
      }).then(r => r.json());
      if (res.kind) {
        setKindSchema({ kind: res.kind, fields: res.fields || [] });
        setKindDraft(res.data || {});
        setEditOpen(true);
        return;
      }
    } catch { /* fall through to the generic form below */ }

    setKindSchema(null);
    // Parse bullets from shapes: lines starting with "1.  ", "2.  " etc.
    const rawBullets: string[] = [];
    (slide.shapes || []).forEach(sh => {
      sh.text.split("\n").forEach(line => {
        const m = line.match(/^\d+\.\s{1,3}(.+)/);
        if (m) rawBullets.push(m[1].trim());
      });
    });
    setEditDraft({
      title:   inferSlideTitle(slide, `Slide ${current + 1}`),
      bullets: rawBullets.length > 0 ? rawBullets : [""],
    });
    setEditOpen(true);
  }

  async function saveEditSlide() {
    const sid = sessionId || getAnonPptSid();
    setSavingEdit(true);
    try {
      const url = kindSchema ? "/api/v1/ppt/slide/kind" : "/api/v1/ppt/slide";
      const body = kindSchema
        ? { session_id: sid, slide_index: current, kind: kindSchema.kind, data: kindDraft, notes: notesDraft }
        : { session_id: sid, slide_index: current, title: editDraft.title,
            bullets: editDraft.bullets.filter(b => b.trim() !== ""), notes: notesDraft };
      const res = await fetch(url, {
        method: "PATCH",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify(body),
      }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });
      const sl: Slide[] = res.slides || [];
      setSlides(sl);
      store.setPptSlides(sl);
      setEditOpen(false);
      setAgentLog(l => [...l, `✓ Saved edits to slide ${current + 1}`]);
    } catch (e: any) {
      setAgentLog(l => [...l, `✗ Edit save failed: ${e.message}`]);
    } finally {
      setSavingEdit(false);
    }
  }

  function handleDeleteSlide() {
    if (slides.length === 0) return;
    const role = (store.user?.role || "").toLowerCase();
    if (role !== "admin") {
      setDeleteModal("denied");
      return;
    }
    setDeleteModal("confirm");
  }

  function confirmDeleteSlide() {
    const updated = slides.filter((_,i) => i !== current);
    // Re-index
    const reindexed = updated.map((s,i) => ({ ...s, index: i }));
    setSlides(reindexed);
    store.setPptSlides(reindexed);
    setCurrent(c => Math.min(c, Math.max(reindexed.length - 1, 0)));
    setAgentLog(l => [...l, `✓ Deleted slide ${current + 1}`]);
    setDeleteModal("hidden");
  }

  // ── Voice input for description field ─────────────────────────────────────
  const toggleMicInput = useCallback(() => {
    const SR = (window as any).SpeechRecognition || (window as any).webkitSpeechRecognition;
    if (!SR) { alert("Speech recognition not supported in this browser. Use Chrome."); return; }
    if (micListening && recognitionRef.current) {
      recognitionRef.current.stop();
      setMicListening(false);
      return;
    }
    const rec = new SR();
    rec.lang = "en-US";
    rec.continuous = false;
    rec.interimResults = false;
    rec.onresult = (e: any) => {
      const heard = e.results[0][0].transcript;
      setGenDesc(prev => prev ? prev + " " + heard : heard);
      setMicListening(false);
    };
    rec.onerror = () => setMicListening(false);
    rec.onend   = () => setMicListening(false);
    recognitionRef.current = rec;
    rec.start();
    setMicListening(true);
  }, [micListening]);

  // ── PPT generation ─────────────────────────────────────────────────────────
  const generatePPT = useCallback(async () => {
    if (!genDesc.trim()) return;
    setGenerating(true);
    setGenProgress(`Generating ${genCount} slides with AI…`);
    const sid = sessionId || getAnonPptSid();
    try {
      const est = Math.round(genCount * 0.6);
      setGenProgress(`Asking Ollama to write ${genCount} slides… (~${est}s)`);
      const res = await fetch("/api/v1/ppt/generate", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, description: genDesc.trim(), slide_count: genCount }),
      }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });

      const sl: Slide[] = res.slides || [];
      setSlides(sl);
      store.setPptSlides(sl);
      store.setPptFileName(res.title || "Generated Presentation");
      setFileName(res.title || "Generated Presentation");
      setCurrent(0);
      setGeneratedSid(sid);
      setCreateModal(false);
      setAgentLog(l => [...l, `✓ Created "${res.title}" — ${sl.length} slides`]);
    } catch (e: any) {
      setGenProgress(`Failed: ${e.message}`);
      setTimeout(() => { setGenerating(false); setGenProgress(""); }, 3000);
      return;
    }
    setGenerating(false);
    setGenProgress("");
    setGenDesc("");
  }, [genDesc, genCount, sessionId, token]);

  // ── Add slide to the loaded presentation ────────────────────────────────
  const addSlide = useCallback(async () => {
    const sid = generatedSid || sessionId || getAnonPptSid();
    setAddingSlide(true);
    setAddSlideError("");
    try {
      const res = await fetch("/api/v1/ppt/slide/add-generate", {
        method: "POST",
        headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
        body: JSON.stringify({ session_id: sid, instruction: addSlideDesc.trim(), insert_after: current }),
      }).then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); });

      const sl: Slide[] = res.slides || [];
      setSlides(sl);
      store.setPptSlides(sl);
      setCurrent(Math.min(current + 1, Math.max(sl.length - 1, 0)));
      setAddSlideModal(false);
      setAddSlideDesc("");
      setAgentLog(l => [...l.slice(-9), `✓ Added slide — ${res.title || "New Slide"}`]);
    } catch (e: any) {
      setAddSlideError(`Failed: ${e.message}`);
    }
    setAddingSlide(false);
  }, [addSlideDesc, current, generatedSid, sessionId, token]);

  const slideTitle = inferSlideTitle(slides[current], `Slide ${current+1}`);
  const slideNotes = slides[current]?.notes || "";

  return (
    <div style={{ flex:1, display:"flex", flexDirection:"column", background:C.bg, overflow:"hidden" }}>
      {/* header */}
      <div style={{ display:"flex", alignItems:"center", gap:"1rem", padding:"0.75rem 1.5rem",
                    background:C.surface, borderBottom:`1.5px solid ${C.border}` }}>
        <div style={{ flex:1 }}>
          <h2 style={{ fontWeight:700, fontSize:"0.95rem", color:C.text1 }}>
            {fileName || "PPT Copilot"}
          </h2>
          <p style={{ fontSize:"0.72rem", color:C.text3 }}>
            {slides.length > 0 ? `${slides.length} slides · Slide ${current+1}: ${slideTitle}` : "Upload a .pptx file to begin"}
          </p>
        </div>

        {/* upload button */}
        <button onClick={() => fileRef.current?.click()}
          style={{ padding:"0.45rem 1rem", borderRadius:8, background:C.amberBg,
                   border:`1.5px solid ${C.amber}`, color:C.amberDark,
                   fontWeight:600, fontSize:"0.8rem", cursor:"pointer",
                   display:"flex", alignItems:"center", gap:"0.4rem" }}>
          {uploading ? "Uploading…" : <><FolderUpIcon size={14}/> Upload .pptx</>}
        </button>
        <input ref={fileRef} type="file" accept=".pptx" style={{ display:"none" }}
          onChange={e => { const f=e.target.files?.[0]; if(f) uploadFile(f); }}/>

        {/* Create PPT button — same amber style as Upload */}
        <button onClick={() => { setCreateModal(true); setGenProgress(""); }}
          style={{ padding:"0.45rem 1rem", borderRadius:8, background:C.amberBg,
                   border:`1.5px solid ${C.amber}`, color:C.amberDark,
                   fontWeight:600, fontSize:"0.8rem", cursor:"pointer" }}>
          Create PPT
        </button>

        {/* Add Slide button — only meaningful once a deck is loaded */}
        {slides.length > 0 && (
          <button onClick={() => { setAddSlideModal(true); setAddSlideError(""); }}
            style={{ padding:"0.45rem 1rem", borderRadius:8, background:C.amberBg,
                     border:`1.5px solid ${C.amber}`, color:C.amberDark,
                     fontWeight:600, fontSize:"0.8rem", cursor:"pointer" }}>
            + Add Slide
          </button>
        )}

        {/* History button */}
        <div style={{ position:"relative" }}>
          <button onClick={() => historyOpen ? setHistoryOpen(false) : openHistory()}
            style={{ padding:"0.45rem 1rem", borderRadius:8, background:C.amberBg,
                     border:`1.5px solid ${C.amber}`, color:C.amberDark,
                     fontWeight:600, fontSize:"0.8rem", cursor:"pointer" }}>
            History
          </button>

          {historyOpen && (
            <div style={{ position:"absolute", top:"calc(100% + 6px)", right:0, zIndex:100,
                          width:340, maxHeight:420, overflowY:"auto",
                          background:C.surface, border:`1.5px solid ${C.border}`,
                          borderRadius:12, boxShadow:"0 8px 32px rgba(0,0,0,0.35)",
                          padding:"0.6rem" }}>
              <div style={{ fontSize:"0.72rem", fontWeight:700, color:C.text3,
                            letterSpacing:"0.06em", padding:"0.25rem 0.4rem 0.5rem" }}>
                GENERATED PRESENTATIONS
              </div>
              {historyLoading && (
                <div style={{ padding:"1rem", textAlign:"center", color:C.text3, fontSize:"0.8rem" }}>
                  Loading…
                </div>
              )}
              {!historyLoading && historyList.length === 0 && (
                <div style={{ padding:"1rem", textAlign:"center", color:C.text3, fontSize:"0.8rem" }}>
                  No presentations generated yet.
                </div>
              )}
              {!historyLoading && historyList.map((entry, i) => (
                <div key={i} onClick={() => loadFromHistory(entry.session_id)}
                  style={{ padding:"0.6rem 0.7rem", borderRadius:8, cursor:"pointer",
                           border:`1px solid ${C.border}`, marginBottom:"0.4rem",
                           background:"transparent", transition:"background 0.15s" }}
                  onMouseEnter={e => (e.currentTarget.style.background = C.amberBg)}
                  onMouseLeave={e => (e.currentTarget.style.background = "transparent")}>
                  <div style={{ fontSize:"0.8rem", fontWeight:600, color:C.text1,
                                overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                    {entry.title}
                  </div>
                  <div style={{ fontSize:"0.68rem", color:C.text3, marginTop:"0.2rem", display:"flex", gap:"0.75rem" }}>
                    <span>{entry.slide_count} slides</span>
                    <span>{new Date(entry.created_at).toLocaleDateString(undefined, { month:"short", day:"numeric", hour:"2-digit", minute:"2-digit" })}</span>
                  </div>
                  {entry.description && (
                    <div style={{ fontSize:"0.66rem", color:C.text3, marginTop:"0.15rem",
                                  overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                      {entry.description}
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Export PDF / Download PPTX buttons — visible whenever slides are loaded */}
        {slides.length > 0 && (() => {
          const sid = generatedSid || sessionId || getAnonPptSid();
          return (
            <>
              <a href={`/api/v1/ppt/export-pdf/${sid}`}
                download
                style={{ padding:"0.45rem 1rem", borderRadius:8, textDecoration:"none",
                         background:"rgba(34,197,94,0.1)", border:"1.5px solid rgba(34,197,94,0.4)",
                         color:"#22C55E", fontWeight:600, fontSize:"0.8rem", cursor:"pointer",
                         display:"flex", alignItems:"center", gap:"0.3rem" }}>
                ⬇ Export PDF
              </a>
              <a href={`/api/v1/ppt/download/${sid}`}
                download
                style={{ padding:"0.45rem 1rem", borderRadius:8, textDecoration:"none",
                         background:C.amberBg, border:`1.5px solid ${C.amber}`,
                         color:C.amberDark, fontWeight:600, fontSize:"0.8rem", cursor:"pointer",
                         display:"flex", alignItems:"center", gap:"0.3rem" }}>
                ⬇ Download PPTX
              </a>
            </>
          );
        })()}

        {/* Remove button — only shown when slides are loaded */}
        {slides.length > 0 && (
          <button onClick={removePresentation}
            style={{ padding:"0.45rem 0.85rem", borderRadius:8,
                     background:"rgba(239,68,68,0.08)", border:"1.5px solid rgba(239,68,68,0.3)",
                     color:"#EF4444", fontWeight:600, fontSize:"0.8rem", cursor:"pointer",
                     display:"flex", alignItems:"center", gap:"0.35rem" }}>
            <XIcon size={13}/> Remove
          </button>
        )}

        {/* listening badge */}
        <div style={{ display:"flex", alignItems:"center", gap:"0.5rem",
                      padding:"0.35rem 0.85rem", background:C.surface,
                      borderRadius:20, border:`1.5px solid ${C.border}`,
                      fontSize:"0.78rem", fontWeight:700, color:C.text1 }}>
          <div style={{ display:"flex", alignItems:"flex-end", gap:"2px" }}>
            {[6,12,8,16,10,14,8].map((h,i)=>(
              <div key={i} style={{ width:3, height:isListening ? h : 3,
                                    background:C.amber, borderRadius:1,
                                    transition:"height 0.15s",
                                    transitionDelay:`${i*0.04}s` }}/>
            ))}
          </div>
          {isListening ? "LISTENING" : "OFFLINE"}
        </div>
      </div>

      {/* paddingBottom reserves clearance for the fixed LiveTranscriptBar (~4.35rem tall)
          overlaid by the parent PPTPageView, matching MainDashboard's 6rem convention. */}
      <div style={{ display:"flex", flex:1, overflow:"hidden", boxSizing:"border-box", paddingBottom:"6rem" }}>
        {/* Main slide area */}
        <div style={{ flex:1, display:"flex", flexDirection:"column", minHeight:0 }}>
          {/* Slide canvas */}
          <div style={{ flex:1, background:C.bg, display:"flex",
                        alignItems:"center", justifyContent:"center",
                        position:"relative", overflow:"hidden", minHeight:0 }}>
            {slides.length === 0 ? (
              /* Drop zone */
              <div onDragOver={e=>e.preventDefault()}
                onDrop={e=>{e.preventDefault();const f=e.dataTransfer.files[0];if(f)uploadFile(f);}}
                onClick={() => fileRef.current?.click()}
                style={{ display:"flex", flexDirection:"column", alignItems:"center",
                         gap:"1rem", cursor:"pointer", userSelect:"none" }}>
                <div style={{ width:80, height:80, borderRadius:16,
                              background:C.amberBg, border:`2px dashed ${C.amber}`,
                              display:"flex", alignItems:"center", justifyContent:"center" }}>
                  <PresentationIcon size={34} color={C.amberDark} strokeWidth={1.5}/>
                </div>
                <p style={{ color:C.text2, fontSize:"0.9rem", textAlign:"center" }}>
                  Drop a .pptx file here<br/>
                  <span style={{ fontSize:"0.75rem", color:C.text3 }}>or click to browse</span>
                </p>
              </div>
            ) : (
              /* Slide canvas — shows converted PNG image (exact copy of original) */
              <div style={{ width:"90%", maxWidth:720, aspectRatio:"16/9",
                            background:C.surface, borderRadius:6,
                            border:`1.5px solid ${C.border}`,
                            boxShadow:"0 8px 36px rgba(0,0,0,0.14)",
                            position:"relative", overflow:"hidden", flexShrink:0 }}>
                {/* Slide counter */}
                <div style={{ position:"absolute", top:8, right:8, zIndex:10,
                              padding:"0.15rem 0.5rem", borderRadius:4,
                              background:"rgba(0,0,0,0.5)",
                              fontSize:"0.58rem", color:"#F5A700", fontWeight:700,
                              letterSpacing:"0.08em" }}>
                  {current+1} / {slides.length}
                </div>

                {canvasMode && slides[current] ? (
                  /* ── WYSIWYG canvas — drag/resize/edit shapes over the PNG ── */
                  <SlideCanvas
                    key={`canvas-${current}`}
                    slide={slides[current]}
                    sid={generatedSid || sessionId || getAnonPptSid()}
                    token={token}
                    onDirty={refreshSlides}
                  />
                ) : slides[current]?.image_url ? (
                  /* ── Faithful image from LibreOffice conversion ── */
                  <img
                    key={slides[current].image_url}
                    src={slides[current].image_url}
                    alt={slideTitle}
                    style={{ width:"100%", height:"100%",
                             objectFit:"contain", display:"block" }}
                  />
                ) : (
                  /* ── Text fallback (LibreOffice not installed) ── */
                  <div style={{ position:"absolute", inset:0,
                                background: slides[current]?.bg_color || C.surface,
                                display:"flex", flexDirection:"column",
                                alignItems:"center", justifyContent:"center",
                                padding:"2rem", gap:"0.5rem" }}>
                    {slides[current]?.shapes && slides[current].shapes!.length > 0
                      ? slides[current].shapes!.map((shape, idx) => (
                          <div key={idx} style={{
                            position:   "absolute",
                            left:       `${shape.left ?? 5}%`,
                            top:        `${shape.top  ?? 10}%`,
                            width:      `${Math.min(shape.width ?? 90, 96)}%`,
                            color:      shape.color || "#fff",
                            fontSize:   `${Math.max(shape.size * 0.75, 8)}px`,
                            fontWeight: shape.bold ? 700 : 400,
                            textAlign:  (shape.align || "left") as "left"|"right"|"center",
                            lineHeight: 1.3, wordBreak:"break-word",
                            whiteSpace: "pre-wrap", overflow:"hidden",
                          }}>
                            {shape.text}
                          </div>
                        ))
                      : (
                        <>
                          <h2 style={{ fontSize:"1.5rem", fontWeight:800, color:"#fff",
                                       textAlign:"center", margin:0 }}>{slideTitle}</h2>
                          {slideNotes && (
                            <p style={{ fontSize:"0.8rem", color:"rgba(255,255,255,0.55)",
                                        textAlign:"center", lineHeight:1.6,
                                        maxWidth:460, margin:0 }}>
                              {slideNotes.substring(0, 200)}
                            </p>
                          )}
                        </>
                      )
                    }
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Thumbnail slider — shows THUMB_COUNT at a time */}
          {slides.length > 0 && (
            <div style={{ display:"flex", alignItems:"center", gap:"0.35rem",
                          padding:"0.5rem 0.75rem", background:C.surface,
                          borderTop:`1.5px solid ${C.border}`, height:72, flexShrink:0 }}>
              {/* Prev arrow */}
              <button onClick={() => setThumbStart(t => Math.max(0, t - 1))}
                disabled={thumbStart === 0}
                style={{ flexShrink:0, width:26, height:40, borderRadius:5,
                         border:`1px solid ${C.border}`, background:C.bg,
                         color: thumbStart === 0 ? C.text3 : C.text2,
                         cursor: thumbStart === 0 ? "default" : "pointer",
                         fontSize:"0.75rem", display:"flex", alignItems:"center", justifyContent:"center" }}>
                ‹
              </button>

              {/* Visible thumbnails */}
              <div style={{ flex:1, display:"flex", gap:"0.35rem", overflow:"hidden" }}>
                {slides.slice(thumbStart, thumbStart + THUMB_COUNT).map((s, rel) => {
                  const i = thumbStart + rel;
                  return (
                    <div key={i} onClick={() => {
                      setCurrent(i);
                      fetch(`/api/v1/ppt/jump`, {
                        method:"POST",
                        headers:{"Content-Type":"application/json", Authorization:`Bearer ${token}`},
                        body: JSON.stringify({session_id: sessionId||getAnonPptSid(), query: `slide ${i+1}`})
                      }).catch(()=>{});
                    }}
                      style={{ flex:1, minWidth:0, height:52, borderRadius:5,
                               background: i===current ? C.amberBg : C.bg,
                               border:`2px solid ${i===current ? C.amber : C.border}`,
                               cursor:"pointer", overflow:"hidden", flexShrink:0,
                               transition:"all 0.18s", position:"relative" }}>
                      {s.image_url ? (
                        <img src={s.image_url} alt={s.title}
                             style={{ width:"100%", height:"100%", objectFit:"cover", display:"block" }}/>
                      ) : (
                        <div style={{ padding:"0.28rem 0.4rem" }}>
                          <div style={{ fontSize:"0.52rem", color:i===current?C.amberDark:"rgba(255,255,255,0.45)", fontWeight:700 }}>
                            {i+1}
                          </div>
                          <div style={{ fontSize:"0.58rem", color:i===current?C.amberDark:"rgba(255,255,255,0.65)",
                                        overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                            {s.title}
                          </div>
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {/* Next arrow */}
              <button onClick={() => setThumbStart(t => Math.min(slides.length - THUMB_COUNT, t + 1))}
                disabled={thumbStart + THUMB_COUNT >= slides.length}
                style={{ flexShrink:0, width:26, height:40, borderRadius:5,
                         border:`1px solid ${C.border}`, background:C.bg,
                         color: thumbStart + THUMB_COUNT >= slides.length ? C.text3 : C.text2,
                         cursor: thumbStart + THUMB_COUNT >= slides.length ? "default" : "pointer",
                         fontSize:"0.75rem", display:"flex", alignItems:"center", justifyContent:"center" }}>
                ›
              </button>

              {/* Slide counter */}
              <div style={{ flexShrink:0, fontSize:"0.65rem", color:"#555",
                            fontWeight:600, minWidth:36, textAlign:"right" }}>
                {current+1}/{slides.length}
              </div>
            </div>
          )}

          {/* Nav controls + mic */}
          <div style={{ display:"flex", gap:"0.5rem", padding:"0.65rem 1rem",
                        background:C.surface, borderTop:`1.5px solid ${C.border}`,
                        alignItems:"center" }}>
            {["first","prev","next","last"].map(d=>(
              <button key={d} onClick={()=>{
                const actions: Record<string,()=>void> = {
                  first:()=>setCurrent(0), last:()=>setCurrent(Math.max(slides.length-1,0)),
                  prev:()=>setCurrent(c=>Math.max(c-1,0)), next:()=>setCurrent(c=>Math.min(c+1,Math.max(slides.length-1,0)))
                };
                actions[d]?.();
                fetch(`/api/v1/ppt/navigate`,{
                  method:"POST",headers:{"Content-Type":"application/json",Authorization:`Bearer ${token}`},
                  body:JSON.stringify({session_id:sessionId||getAnonPptSid(),direction:d})
                }).catch(()=>{});
              }}
                style={{ padding:"0.4rem 0.85rem", borderRadius:6,
                         border:`1.5px solid ${C.border}`, background:"transparent",
                         color:C.text2, cursor:"pointer", fontSize:"0.78rem" }}>
                {d}
              </button>
            ))}
            <div style={{ flex:1 }}/>
            {/* Canvas / Preview toggle */}
            {slides.length > 0 && (
              <div style={{ display:"flex", background:"var(--bg2)", border:`1.5px solid ${C.border}`,
                            borderRadius:8, padding:2, marginRight:"0.15rem" }}>
                {([["Preview",false],["Canvas",true]] as const).map(([label,on])=>(
                  <button key={label} onClick={()=>setCanvasMode(on)}
                    style={{ padding:"0.3rem 0.7rem", borderRadius:6, border:"none", cursor:"pointer",
                             fontSize:"0.74rem", fontWeight:600,
                             background: canvasMode===on ? C.amber : "transparent",
                             color: canvasMode===on ? "#fff" : C.text2 }}>
                    {label}
                  </button>
                ))}
              </div>
            )}
            {/* Delete slide — admin only */}
            {slides.length > 0 && (
              <button onClick={handleDeleteSlide}
                style={{ padding:"0.4rem 0.85rem", borderRadius:6,
                         background:"rgba(239,68,68,0.08)", border:"1.5px solid rgba(239,68,68,0.25)",
                         color:"#EF4444", cursor:"pointer", fontSize:"0.78rem", fontWeight:600,
                         display:"flex", alignItems:"center", gap:"0.35rem" }}>
                <TrashIcon size={12}/> Delete Slide
              </button>
            )}
            <span style={{ fontSize:"0.7rem", color:C.text3 }}>Say "Go to slide 42"</span>
          </div>

          {/* ── Speaker Notes ── moved below the slide viewer: this is the natural
              reading position for notes (directly under what they annotate),
              and gives the text far more horizontal room than the 290px sidebar
              could — long notes no longer wrap into a tall, narrow column. ── */}
          {slides.length > 0 && (
            <div style={{ padding:"0.75rem 1rem", background:C.surface,
                          borderTop:`1.5px solid ${C.border}` }}>
              <div style={{ display:"flex", alignItems:"center", justifyContent:"space-between",
                            marginBottom:"0.4rem" }}>
                <div style={{ fontWeight:700, fontSize:"0.75rem", color:C.amber,
                              letterSpacing:"0.06em" }}>SPEAKER NOTES</div>
                <button onClick={openEditModal}
                  title="Edit slide notes"
                  style={{ fontSize:"0.68rem", color:C.text3, background:"none", border:"none",
                           cursor:"pointer", padding:"0.1rem 0.3rem", borderRadius:4,
                           transition:"color 0.15s",
                           display:"flex", alignItems:"center", gap:"0.25rem" }}
                  onMouseEnter={e => (e.currentTarget.style.color = C.amber)}
                  onMouseLeave={e => (e.currentTarget.style.color = C.text3)}>
                  <PencilIcon size={10}/> edit
                </button>
              </div>
              <div style={{ background:C.bg, borderRadius:8, padding:"0.6rem 0.75rem",
                            border:`1.5px solid ${C.border}`, maxHeight:110, overflowY:"auto",
                            fontSize:"0.78rem", color:C.text2, lineHeight:1.6,
                            whiteSpace:"pre-wrap", wordBreak:"break-word" }}>
                {slides[current]?.notes
                  ? slides[current].notes
                  : <span style={{ color:C.text3, fontStyle:"italic" }}>No notes for this slide.</span>
                }
              </div>
            </div>
          )}
        </div>

        {/* Agent activity panel */}
        <div style={{ width:290, background:C.surface, borderLeft:`1.5px solid ${C.border}`,
                      padding:"1rem", overflowY:"auto", flexShrink:0, display:"flex",
                      flexDirection:"column", gap:"1rem" }}>

          {/* Status */}
          <div>
            <div style={{ fontWeight:700, fontSize:"0.82rem", color:C.text1, marginBottom:"0.6rem",
                          letterSpacing:"0.04em" }}>
              AGENT ACTIVITY
            </div>
            <div style={{ display:"flex", alignItems:"center", gap:"0.4rem",
                          padding:"0.5rem 0.7rem", borderRadius:8,
                          background: isListening ? C.amberBg : C.surface,
                          border:`1.5px solid ${isListening ? C.amber : C.border}` }}>
              <div style={{ width:7, height:7, borderRadius:"50%", flexShrink:0,
                            background: isListening ? C.amber : C.border,
                            animation: isListening ? "pulse 1.2s infinite" : "none" }}/>
              <div style={{ flex:1 }}>
                <div style={{ fontSize:"0.76rem", fontWeight:600, color:C.text1 }}>
                  {isListening ? "Listening" : "Idle"}
                </div>
                {isListening && (
                  <div style={{ fontSize:"0.66rem", color:C.text3, marginTop:"0.1rem" }}>{agentStatus}</div>
                )}
              </div>
            </div>
          </div>

          {/* Tool calls */}
          {(() => {
            const pptTools = store.toolCards.filter(c =>
              ["ppt_navigate","ppt_jump_to_title","ppt_summarize"].includes(c.tool)
            ).slice(-6).reverse();
            return pptTools.length > 0 ? (
              <div>
                <div style={{ fontSize:"0.68rem", fontWeight:700, color:C.text3,
                              letterSpacing:"0.07em", marginBottom:"0.4rem" }}>TOOL CALLS</div>
                {pptTools.map((c,i) => (
                  <div key={i} style={{ display:"flex", alignItems:"flex-start", gap:"0.4rem",
                                        marginBottom:"0.45rem" }}>
                    <div style={{ width:14, height:14, borderRadius:"50%", flexShrink:0, marginTop:1,
                                  display:"flex", alignItems:"center", justifyContent:"center",
                                  color:"#fff",
                                  background: c.status==="ok" ? "#22C55E"
                                            : c.status==="running" ? C.amber : C.border }}>
                      {c.status==="ok" ? <CheckIcon size={9} strokeWidth={3}/>
                       : c.status==="running" ? <DotIcon size={6}/> : <DotIcon size={6} filled={false}/>}
                    </div>
                    <div style={{ flex:1 }}>
                      <div style={{ fontSize:"0.74rem", fontWeight:600, color:C.text1 }}>
                        {c.tool.replace("ppt_","").replace(/_/g," ")}
                      </div>
                      <div style={{ fontSize:"0.66rem", color: c.status==="running" ? C.amber : C.text3 }}>
                        {c.status==="running" ? "Running…"
                         : c.status==="ok" ? `Done · ${c.latency_ms ? c.latency_ms+"ms" : ""}` : "Pending"}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            ) : null;
          })()}

          {/* Nav log */}
          <div>
            <div style={{ fontSize:"0.68rem", fontWeight:700, color:C.text3,
                          letterSpacing:"0.07em", marginBottom:"0.4rem" }}>NAVIGATION LOG</div>
            {agentLog.length === 0
              ? <div style={{ fontSize:"0.72rem", color:C.text3 }}>No actions yet.</div>
              : agentLog.slice().reverse().map((l,i) => (
                  <div key={i} style={{ display:"flex", gap:"0.35rem", marginBottom:"0.4rem",
                                        alignItems:"flex-start" }}>
                    <span style={{ color:"#22C55E", flexShrink:0, fontSize:"0.72rem", marginTop:"0.05rem" }}>›</span>
                    <div style={{ fontSize:"0.72rem", lineHeight:1.45, color:C.text2 }}>{l}</div>
                  </div>
                ))
            }
          </div>
        </div>
      </div>

      {/* Edit Slide modal */}
      {editOpen && (
        <div style={{ position:"absolute", inset:0, background:"rgba(0,0,0,0.6)",
                      display:"flex", alignItems:"center", justifyContent:"center", zIndex:60 }}>
          <div style={{ background:C.surface, borderRadius:16, padding:"1.75rem",
                        maxWidth:520, width:"92%", maxHeight:"86vh", overflowY:"auto",
                        boxShadow:"0 12px 56px rgba(0,0,0,0.45)" }}>

            <div style={{ fontWeight:700, fontSize:"1rem", color:C.text1, marginBottom:"0.25rem",
                          display:"flex", alignItems:"center", gap:"0.4rem" }}>
              <PencilIcon size={15}/> Edit Slide {current + 1}
            </div>
            <p style={{ fontSize:"0.76rem", color:C.text3, marginBottom:"1.25rem" }}>
              Changes are saved to the .pptx file and thumbnails are regenerated.
            </p>

            {kindSchema ? (
              kindSchema.fields.length === 0 ? (
                <p style={{ fontSize:"0.8rem", color:C.text3, fontStyle:"italic", marginBottom:"1rem" }}>
                  This slide has no editable fields — it's a fixed closing slide.
                </p>
              ) : (
                kindSchema.fields.map(field => (
                  <div key={field.key} style={{ marginBottom:"0.2rem" }}>
                    <label style={{ fontSize:"0.78rem", fontWeight:600, color:C.text2, display:"block", marginBottom:"0.35rem" }}>
                      {field.label || field.key}
                    </label>
                    {field.type === "table_grid" ? (
                      // The "table" kind's extract/populate functions read/write
                      // "headers"/"rows" at the TOP level of the data dict (matching
                      // the generation-time content schema), not nested under a
                      // "table" key the way every other field's `key` maps 1:1 onto
                      // a data property — bind directly to those two keys instead.
                      <KindFieldEditor
                        field={field}
                        value={{ headers: kindDraft?.headers, rows: kindDraft?.rows }}
                        onChange={v => setKindDraft((d: any) => ({ ...(d || {}), headers: v.headers, rows: v.rows }))}
                        C={C}
                      />
                    ) : (
                      <KindFieldEditor
                        field={field}
                        value={kindDraft?.[field.key]}
                        onChange={v => setKindDraft((d: any) => ({ ...(d || {}), [field.key]: v }))}
                        C={C}
                      />
                    )}
                  </div>
                ))
              )
            ) : (
              <>
                {/* Title */}
                <label style={{ fontSize:"0.78rem", fontWeight:600, color:C.text2, display:"block", marginBottom:"0.35rem" }}>
                  Slide Title
                </label>
                <input
                  value={editDraft.title}
                  onChange={e => setEditDraft(d => ({ ...d, title: e.target.value }))}
                  placeholder="Slide title"
                  style={{ width:"100%", boxSizing:"border-box", padding:"0.55rem 0.75rem",
                           borderRadius:8, border:`1.5px solid ${C.border}`,
                           background:C.bg, color:C.text1, fontSize:"0.88rem",
                           outline:"none", marginBottom:"1.1rem", fontFamily:"inherit" }}
                />

                {/* Bullets */}
                <label style={{ fontSize:"0.78rem", fontWeight:600, color:C.text2, display:"block", marginBottom:"0.35rem" }}>
                  Bullet Points
                </label>
                {editDraft.bullets.map((b, i) => (
                  <div key={i} style={{ display:"flex", gap:"0.4rem", marginBottom:"0.4rem", alignItems:"flex-start" }}>
                    <span style={{ marginTop:"0.55rem", color:C.text3, fontSize:"0.72rem",
                                   minWidth:"1.2rem", textAlign:"right" }}>{i + 1}.</span>
                    <textarea
                      value={b}
                      rows={2}
                      onChange={e => setEditDraft(d => {
                        const bullets = [...d.bullets];
                        bullets[i] = e.target.value;
                        return { ...d, bullets };
                      })}
                      placeholder={`Bullet ${i + 1}`}
                      style={{ flex:1, padding:"0.45rem 0.65rem", borderRadius:6,
                               border:`1.5px solid ${C.border}`, background:C.bg,
                               color:C.text1, fontSize:"0.8rem", resize:"vertical",
                               fontFamily:"inherit", outline:"none" }}
                    />
                    <button
                      onClick={() => setEditDraft(d => ({ ...d, bullets: d.bullets.filter((_,j) => j !== i) }))}
                      disabled={editDraft.bullets.length <= 1}
                      style={{ marginTop:"0.4rem", width:22, height:22, borderRadius:"50%", border:"none",
                               background:"rgba(239,68,68,0.15)", color:"#EF4444",
                               cursor: editDraft.bullets.length <= 1 ? "default" : "pointer",
                               opacity: editDraft.bullets.length <= 1 ? 0.35 : 1,
                               flexShrink:0, display:"flex", alignItems:"center", justifyContent:"center" }}>
                      <XIcon size={11}/>
                    </button>
                  </div>
                ))}
                <button
                  onClick={() => setEditDraft(d => ({ ...d, bullets: [...d.bullets, ""] }))}
                  style={{ fontSize:"0.76rem", color:C.amber, background:"none", border:"none",
                           cursor:"pointer", padding:"0.25rem 0", marginBottom:"1.1rem" }}>
                  + Add bullet
                </button>
              </>
            )}

            {/* Speaker Notes — a universal field, present on every kind and on
                uploaded decks alike, so it's shared by both edit modes above. */}
            <label style={{ fontSize:"0.78rem", fontWeight:600, color:C.text2, display:"block", marginBottom:"0.35rem" }}>
              Speaker Notes
            </label>
            <textarea
              value={notesDraft}
              rows={4}
              onChange={e => setNotesDraft(e.target.value)}
              placeholder="Notes visible to the presenter during the presentation…"
              style={{ width:"100%", boxSizing:"border-box", padding:"0.55rem 0.75rem",
                       borderRadius:8, border:`1.5px solid ${C.border}`,
                       background:C.bg, color:C.text1, fontSize:"0.8rem",
                       resize:"vertical", fontFamily:"inherit", outline:"none",
                       marginBottom:"1.4rem" }}
            />

            {/* Regeneration note */}
            {savingEdit && (
              <div style={{ fontSize:"0.76rem", color:C.amber, marginBottom:"1rem",
                            display:"flex", alignItems:"center", gap:"0.5rem" }}>
                <span style={{ display:"inline-block", animation:"spin 1s linear infinite" }}>⟳</span>
                Saving and regenerating slide thumbnails… (~5–10s)
              </div>
            )}

            {/* Buttons */}
            <div style={{ display:"flex", gap:"0.75rem" }}>
              <button onClick={() => { setEditOpen(false); setSavingEdit(false); }}
                disabled={savingEdit}
                style={{ flex:1, padding:"0.6rem", borderRadius:8,
                         border:`1.5px solid ${C.border}`, background:C.surface,
                         color:C.text1, cursor:"pointer", fontSize:"0.88rem",
                         opacity: savingEdit ? 0.5 : 1 }}>
                Cancel
              </button>
              <button onClick={saveEditSlide}
                disabled={savingEdit || (!kindSchema && !editDraft.title.trim())}
                style={{ flex:2, padding:"0.6rem", borderRadius:8, border:"none",
                         background: savingEdit || (!kindSchema && !editDraft.title.trim())
                           ? "rgba(245,167,0,0.35)" : C.amber,
                         color:"#fff", fontWeight:700, fontSize:"0.88rem",
                         cursor: savingEdit || (!kindSchema && !editDraft.title.trim()) ? "default" : "pointer" }}>
                {savingEdit ? "Saving…" : "Save Changes"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Create PPT modal */}
      {createModal && (
        <div style={{ position:"absolute", inset:0, background:"rgba(0,0,0,0.55)",
                      display:"flex", alignItems:"center", justifyContent:"center", zIndex:50 }}>
          <div style={{ background:C.surface, borderRadius:16, padding:"1.75rem",
                        maxWidth:440, width:"90%", boxShadow:"0 8px 40px rgba(0,0,0,0.25)" }}>

            <div style={{ fontWeight:700, fontSize:"1rem", color:C.text1, marginBottom:"0.25rem" }}>
              Create Presentation
            </div>
            <p style={{ fontSize:"0.78rem", color:C.text3, marginBottom:"1.2rem" }}>
              Describe your topic — PILOT will generate the slides with AI.
            </p>

            {/* Description field + mic */}
            <div style={{ position:"relative", marginBottom:"1rem" }}>
              <textarea
                value={genDesc}
                onChange={e => setGenDesc(e.target.value)}
                placeholder="e.g. Introduction to Machine Learning for beginners, covering supervised learning, neural networks, and real-world applications"
                rows={4}
                style={{ width:"100%", boxSizing:"border-box", padding:"0.65rem 2.8rem 0.65rem 0.75rem",
                         borderRadius:8, border:`1.5px solid ${C.border}`,
                         background:C.bg, color:C.text1, fontSize:"0.82rem",
                         resize:"vertical", fontFamily:"inherit", outline:"none" }}
              />
              {/* In-field mic button */}
              <button onClick={toggleMicInput}
                title={micListening ? "Stop listening" : "Speak description"}
                style={{ position:"absolute", bottom:10, right:8, width:28, height:28,
                         borderRadius:"50%", border:"none", cursor:"pointer",
                         background: micListening ? C.amber : C.amberBg,
                         display:"flex", alignItems:"center",
                         justifyContent:"center", flexShrink:0 }}>
                {micListening ? <StopIcon size={12} color="#fff"/> : <MicIcon size={14} color={C.amberDark}/>}
              </button>
            </div>

            {/* Slide count */}
            <div style={{ display:"flex", alignItems:"center", gap:"0.75rem", marginBottom:"1.4rem" }}>
              <label style={{ fontSize:"0.8rem", color:C.text2, whiteSpace:"nowrap" }}>
                Number of slides
              </label>
              <input
                type="number" min={3} max={20} value={genCount}
                onChange={e => setGenCount(Math.max(3, Math.min(20, Number(e.target.value))))}
                style={{ width:70, padding:"0.4rem 0.6rem", borderRadius:6,
                         border:`1.5px solid ${C.border}`, background:C.bg,
                         color:C.text1, fontSize:"0.85rem", textAlign:"center" }}
              />
              <span style={{ fontSize:"0.72rem", color:C.text3 }}>
                ~{Math.round(genCount * 1.2)}s estimate
              </span>
            </div>

            {/* Progress / error */}
            {genProgress && (
              <div style={{ fontSize:"0.78rem", color: genProgress.startsWith("Failed")
                              ? "#EF4444" : C.amber,
                            marginBottom:"1rem", display:"flex", alignItems:"center", gap:"0.5rem" }}>
                {!genProgress.startsWith("Failed") && (
                  <span style={{ display:"inline-block", animation:"spin 1s linear infinite" }}>⟳</span>
                )}
                {genProgress}
              </div>
            )}

            {/* Buttons */}
            <div style={{ display:"flex", gap:"0.75rem" }}>
              <button onClick={() => { setCreateModal(false); setGenProgress(""); }}
                disabled={generating}
                style={{ flex:1, padding:"0.6rem", borderRadius:8,
                         border:`1.5px solid ${C.border}`, background:C.surface,
                         color:C.text1, cursor:"pointer", fontSize:"0.88rem",
                         opacity: generating ? 0.5 : 1 }}>
                Cancel
              </button>
              <button onClick={generatePPT}
                disabled={generating || !genDesc.trim()}
                style={{ flex:2, padding:"0.6rem", borderRadius:8, border:"none",
                         background: generating || !genDesc.trim()
                           ? "rgba(255,184,0,0.35)" : "#FFB800",
                         color:"#fff", fontWeight:700, fontSize:"0.88rem",
                         cursor: generating || !genDesc.trim() ? "default" : "pointer" }}>
                {generating ? "Generating…" : `Generate ${genCount} Slides`}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Add Slide modal */}
      {addSlideModal && (
        <div style={{ position:"absolute", inset:0, background:"rgba(0,0,0,0.55)",
                      display:"flex", alignItems:"center", justifyContent:"center", zIndex:50 }}>
          <div style={{ background:C.surface, borderRadius:16, padding:"1.75rem",
                        maxWidth:440, width:"90%", boxShadow:"0 8px 40px rgba(0,0,0,0.25)" }}>

            <div style={{ fontWeight:700, fontSize:"1rem", color:C.text1, marginBottom:"0.25rem" }}>
              Add Slide
            </div>
            <p style={{ fontSize:"0.78rem", color:C.text3, marginBottom:"1.2rem" }}>
              Describe what the new slide should be about — it will be inserted
              right after slide {current + 1}, styled to match the rest of the deck.
            </p>

            <textarea
              value={addSlideDesc}
              onChange={e => setAddSlideDesc(e.target.value)}
              placeholder="e.g. Our Q4 roadmap, covering the three main initiatives"
              rows={3}
              style={{ width:"100%", boxSizing:"border-box", padding:"0.65rem 0.75rem",
                       borderRadius:8, border:`1.5px solid ${C.border}`,
                       background:C.bg, color:C.text1, fontSize:"0.82rem",
                       resize:"vertical", fontFamily:"inherit", outline:"none",
                       marginBottom:"1rem" }}
            />

            {addSlideError && (
              <div style={{ fontSize:"0.78rem", color:"#EF4444", marginBottom:"1rem" }}>
                {addSlideError}
              </div>
            )}

            <div style={{ display:"flex", gap:"0.75rem" }}>
              <button onClick={() => { setAddSlideModal(false); setAddSlideError(""); }}
                disabled={addingSlide}
                style={{ flex:1, padding:"0.6rem", borderRadius:8,
                         border:`1.5px solid ${C.border}`, background:C.surface,
                         color:C.text1, cursor:"pointer", fontSize:"0.88rem",
                         opacity: addingSlide ? 0.5 : 1 }}>
                Cancel
              </button>
              <button onClick={addSlide}
                disabled={addingSlide}
                style={{ flex:2, padding:"0.6rem", borderRadius:8, border:"none",
                         background: addingSlide ? "rgba(255,184,0,0.35)" : "#FFB800",
                         color:"#fff", fontWeight:700, fontSize:"0.88rem",
                         cursor: addingSlide ? "default" : "pointer" }}>
                {addingSlide ? "Adding…" : "Add Slide"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Delete slide modals */}
      {deleteModal !== "hidden" && (
        <div style={{ position:"absolute", inset:0, background:"rgba(0,0,0,0.45)",
                      display:"flex", alignItems:"center", justifyContent:"center", zIndex:50 }}>
          <div style={{ background:C.surface, borderRadius:16, padding:"1.75rem",
                        maxWidth:360, width:"90%", boxShadow:"0 8px 40px rgba(0,0,0,0.18)" }}>
            {deleteModal === "denied" ? (
              <>
                <IconBadge size={44} tone="neutral" style={{ marginBottom:"0.8rem" }}><LockIcon size={20}/></IconBadge>
                <div style={{ fontWeight:700, fontSize:"1rem", color:"#EF4444", marginBottom:"0.4rem" }}>
                  Access Denied
                </div>
                <p style={{ fontSize:"0.85rem", color:C.text2, marginBottom:"1.25rem", lineHeight:1.6 }}>
                  Deleting slides requires <strong>Level 3 Admin access</strong>. Your current access level does not permit this action.
                </p>
                <button onClick={() => setDeleteModal("hidden")}
                  style={{ width:"100%", padding:"0.6rem", borderRadius:8, border:"none",
                           background:"#EF4444", color:"#fff", fontWeight:600,
                           fontSize:"0.88rem", cursor:"pointer" }}>
                  OK
                </button>
              </>
            ) : (
              <>
                <IconBadge size={44} tone="red" style={{ marginBottom:"0.8rem" }}>
                  <TrashIcon size={20}/>
                </IconBadge>
                <div style={{ fontWeight:700, fontSize:"1rem", marginBottom:"0.4rem" }}>
                  Delete Slide {current + 1}?
                </div>
                <p style={{ fontSize:"0.85rem", color:C.text2, marginBottom:"1.25rem", lineHeight:1.6 }}>
                  You are about to delete <strong>"{slideTitle}"</strong>. This action cannot be undone.
                </p>
                <div style={{ display:"flex", gap:"0.75rem" }}>
                  <button onClick={() => setDeleteModal("hidden")}
                    style={{ flex:1, padding:"0.6rem", borderRadius:8,
                             border:`1.5px solid ${C.border}`, background:C.surface,
                             color:C.text1, cursor:"pointer", fontSize:"0.88rem" }}>
                    No, Keep It
                  </button>
                  <button onClick={confirmDeleteSlide}
                    style={{ flex:1, padding:"0.6rem", borderRadius:8, border:"none",
                             background:"#EF4444", color:"#fff", fontWeight:600,
                             fontSize:"0.88rem", cursor:"pointer" }}>
                    Yes, Delete
                  </button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
