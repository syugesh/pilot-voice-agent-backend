/**
 * PPT Copilot — full view with:
 * - File upload (.pptx)
 * - Slide list with smooth navigation
 * - Voice "go to slide 42" support
 * - Agent activity panel
 */
import { useState, useRef, useEffect } from "react";
import { useAppStore } from "../store/SessionStore";

function useThemeColors() {
  const theme = useAppStore(s => s.theme);
  return theme === "dark"
    ? { bg:"#0F0F0F", surface:"#1A1A1A", border:"#2A2A2A", text1:"#F0F0F0", text2:"#AAA", text3:"#666", amber:"#F5A700", amberDark:"#D4900F", amberBg:"rgba(245,167,0,0.12)" }
    : { bg:"#F7F6F3", surface:"#FFFFFF", border:"#E5E2DA", text1:"#1A1A1A", text2:"#555", text3:"#888", amber:"#F5A700", amberDark:"#7C5E00", amberBg:"#FFF8E7" };
}

interface SlideShape { text:string; color:string; size:number; bold:boolean; left?:number; top?:number; width?:number; align?:string; }
interface Slide { index:number; title:string; notes:string; bg_color?:string; shapes?:SlideShape[]; }

export function PPTCopilotView({ sessionId, isListening, agentStatus, onToggleMic }:
  { sessionId:string|null; isListening:boolean; agentStatus:string; onToggleMic:()=>void }) {
  const store  = useAppStore();
  const C      = useThemeColors();
  const token  = store.token;
  // Restore from store so slides survive tab switches
  const [slides,     setSlides]     = useState<Slide[]>((store.pptSlides as Slide[]) || []);
  const [current,    setCurrent]    = useState(0);
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

  // Listen for ppt_command events (navigation + voice-triggered delete)
  useEffect(() => {
    const handler = (e: CustomEvent) => {
      const { action, index } = e.detail;
      if (action === "goto" && index !== undefined) {
        setCurrent(Math.max(0, Math.min(index, Math.max(slides.length-1, 49))));
        setAgentLog(l => [...l.slice(-9), `Navigated to slide ${index+1}`]);
      } else if (action === "next")   setCurrent(c => Math.min(c+1, Math.max(slides.length-1,49)));
      else if (action === "prev")     setCurrent(c => Math.max(c-1, 0));
      else if (action === "first")    setCurrent(0);
      else if (action === "last")     setCurrent(Math.max(slides.length-1, 0));
      else if (action === "delete")   setDeleteModal("confirm");  // policy already confirmed admin
    };
    window.addEventListener("ppt_command" as any, handler);
    return () => window.removeEventListener("ppt_command" as any, handler);
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
      const sid = sessionId || "default";
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
  const [deleteModal, setDeleteModal] = useState<"hidden"|"denied"|"confirm">("hidden");

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

  const slideTitle = slides[current]?.title || `Slide ${current+1}`;
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
                   fontWeight:600, fontSize:"0.8rem", cursor:"pointer" }}>
          {uploading ? "Uploading…" : "📁 Upload .pptx"}
        </button>
        <input ref={fileRef} type="file" accept=".pptx" style={{ display:"none" }}
          onChange={e => { const f=e.target.files?.[0]; if(f) uploadFile(f); }}/>
        {/* Remove button — only shown when slides are loaded */}
        {slides.length > 0 && (
          <button onClick={removePresentation}
            style={{ padding:"0.45rem 0.85rem", borderRadius:8,
                     background:"rgba(239,68,68,0.08)", border:"1.5px solid rgba(239,68,68,0.3)",
                     color:"#EF4444", fontWeight:600, fontSize:"0.8rem", cursor:"pointer" }}>
            ✕ Remove
          </button>
        )}

        {/* listening badge */}
        <div style={{ display:"flex", alignItems:"center", gap:"0.5rem",
                      padding:"0.35rem 0.85rem", background:"#F9F8F6",
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

      <div style={{ display:"flex", flex:1, overflow:"hidden" }}>
        {/* Main slide area */}
        <div style={{ flex:1, display:"flex", flexDirection:"column" }}>
          {/* Slide canvas */}
          <div style={{ flex:1, background:"#1A1A1A", display:"flex",
                        alignItems:"center", justifyContent:"center",
                        position:"relative", overflow:"hidden" }}>
            {slides.length === 0 ? (
              /* Drop zone */
              <div onDragOver={e=>e.preventDefault()}
                onDrop={e=>{e.preventDefault();const f=e.dataTransfer.files[0];if(f)uploadFile(f);}}
                onClick={() => fileRef.current?.click()}
                style={{ display:"flex", flexDirection:"column", alignItems:"center",
                         gap:"1rem", cursor:"pointer", userSelect:"none" }}>
                <div style={{ width:80, height:80, borderRadius:16,
                              background:"rgba(245,167,0,0.15)", border:"2px dashed rgba(245,167,0,0.4)",
                              display:"flex", alignItems:"center", justifyContent:"center", fontSize:"2rem" }}>📊</div>
                <p style={{ color:"#888", fontSize:"0.9rem", textAlign:"center" }}>
                  Drop a .pptx file here<br/>
                  <span style={{ fontSize:"0.75rem", color:"#555" }}>or click to browse</span>
                </p>
              </div>
            ) : (
              /* Slide canvas — faithful to original PPTX layout */
              <div style={{ width:"90%", maxWidth:720, aspectRatio:"16/9",
                            background: slides[current]?.bg_color || "#1a1a2e",
                            borderRadius:6,
                            border:"2px solid rgba(255,255,255,0.08)",
                            boxShadow:"0 8px 48px rgba(0,0,0,0.7)",
                            position:"relative",
                            overflow:"hidden",
                            flexShrink:0,
                            transition:"background 0.25s" }}>
                {/* Slide counter */}
                <div style={{ position:"absolute", top:8, right:8, zIndex:10,
                              padding:"0.15rem 0.5rem", borderRadius:4,
                              background:"rgba(0,0,0,0.45)",
                              fontSize:"0.58rem", color:"#F5A700", fontWeight:700,
                              letterSpacing:"0.08em", backdropFilter:"blur(4px)" }}>
                  {current+1} / {slides.length}
                </div>
                {/* Shapes rendered at their exact PPTX positions */}
                {slides[current]?.shapes && slides[current].shapes!.length > 0
                  ? slides[current].shapes!.map((shape, idx) => (
                      <div key={idx} style={{
                        position:   "absolute",
                        left:       `${shape.left ?? 5}%`,
                        top:        `${shape.top  ?? 10}%`,
                        width:      `${Math.min(shape.width ?? 90, 96)}%`,
                        color:      shape.color || "#ffffff",
                        fontSize:   `${Math.max(shape.size * 0.75, 8)}px`,
                        fontWeight: shape.bold ? 700 : 400,
                        textAlign:  (shape.align || "left") as "left"|"right"|"center",
                        lineHeight: 1.3,
                        wordBreak:  "break-word",
                        whiteSpace: "pre-wrap",
                        overflow:   "hidden",
                        pointerEvents: "none",
                      }}>
                        {shape.text}
                      </div>
                    ))
                  : (
                    /* Fallback: no shape data — show title + notes centered */
                    <div style={{ position:"absolute", inset:0, display:"flex",
                                  flexDirection:"column", alignItems:"center",
                                  justifyContent:"center", padding:"2rem", gap:"0.6rem" }}>
                      <h2 style={{ fontSize:"1.6rem", fontWeight:800, color:"#fff",
                                   textAlign:"center", margin:0, letterSpacing:"-0.02em" }}>
                        {slideTitle}
                      </h2>
                      {slideNotes && (
                        <p style={{ fontSize:"0.85rem", color:"rgba(255,255,255,0.6)",
                                    textAlign:"center", lineHeight:1.6,
                                    maxWidth:480, margin:0 }}>
                          {slideNotes.substring(0, 200)}
                        </p>
                      )}
                    </div>
                  )
                }
              </div>
            )}
          </div>

          {/* Thumbnail slider — shows THUMB_COUNT at a time */}
          {slides.length > 0 && (
            <div style={{ display:"flex", alignItems:"center", gap:"0.35rem",
                          padding:"0.5rem 0.75rem", background:"#111",
                          borderTop:"1px solid #222", height:72, flexShrink:0 }}>
              {/* Prev arrow */}
              <button onClick={() => setThumbStart(t => Math.max(0, t - 1))}
                disabled={thumbStart === 0}
                style={{ flexShrink:0, width:26, height:40, borderRadius:5,
                         border:"1px solid #333", background:"#1A1A1A",
                         color: thumbStart === 0 ? "#444" : "#AAA",
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
                        body: JSON.stringify({session_id: sessionId||"default", query: `slide ${i+1}`})
                      }).catch(()=>{});
                    }}
                      style={{ flex:1, minWidth:0, height:52, borderRadius:5,
                               background: i===current ? C.amberBg : (s.bg_color || "#2A2A2A"),
                               border:`2px solid ${i===current ? C.amber : "#333"}`,
                               cursor:"pointer", padding:"0.28rem 0.4rem",
                               transition:"all 0.18s", overflow:"hidden", flexShrink:0 }}>
                      <div style={{ fontSize:"0.52rem", color:i===current?C.amberDark:"rgba(255,255,255,0.45)",
                                    fontWeight:700 }}>
                        {i+1}
                      </div>
                      <div style={{ fontSize:"0.58rem", color:i===current?C.amberDark:"rgba(255,255,255,0.65)",
                                    overflow:"hidden", textOverflow:"ellipsis", whiteSpace:"nowrap" }}>
                        {s.title}
                      </div>
                    </div>
                  );
                })}
              </div>

              {/* Next arrow */}
              <button onClick={() => setThumbStart(t => Math.min(slides.length - THUMB_COUNT, t + 1))}
                disabled={thumbStart + THUMB_COUNT >= slides.length}
                style={{ flexShrink:0, width:26, height:40, borderRadius:5,
                         border:"1px solid #333", background:"#1A1A1A",
                         color: thumbStart + THUMB_COUNT >= slides.length ? "#444" : "#AAA",
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
                  body:JSON.stringify({session_id:sessionId||"default",direction:d})
                }).catch(()=>{});
              }}
                style={{ padding:"0.4rem 0.85rem", borderRadius:6,
                         border:`1.5px solid ${C.border}`, background:"transparent",
                         color:C.text2, cursor:"pointer", fontSize:"0.78rem" }}>
                {d}
              </button>
            ))}
            <div style={{ flex:1 }}/>
            {/* Delete slide — admin only */}
            {slides.length > 0 && (
              <button onClick={handleDeleteSlide}
                style={{ padding:"0.4rem 0.85rem", borderRadius:6,
                         background:"rgba(239,68,68,0.08)", border:"1.5px solid rgba(239,68,68,0.25)",
                         color:"#EF4444", cursor:"pointer", fontSize:"0.78rem", fontWeight:600 }}>
                🗑 Delete Slide
              </button>
            )}
            <button onClick={onToggleMic}
              style={{ width:40, height:40, borderRadius:"50%",
                       background:isListening?C.amber:"#F0EDE8", border:"none",
                       fontSize:"1rem", cursor:"pointer",
                       boxShadow:isListening?`0 0 0 6px rgba(245,167,0,0.2)`:"none",
                       transition:"all 0.25s" }}>
              {isListening?"⏹":"🎤"}
            </button>
            <span style={{ fontSize:"0.7rem", color:C.text3 }}>Say "Go to slide 42"</span>
          </div>
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
                          background: isListening ? C.amberBg : "#F9F8F6",
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
                                  fontSize:"0.45rem", fontWeight:700, color:"#fff",
                                  background: c.status==="ok" ? "#22C55E"
                                            : c.status==="running" ? C.amber : C.border }}>
                      {c.status==="ok" ? "✓" : c.status==="running" ? "●" : "○"}
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

      {/* Delete slide modals */}
      {deleteModal !== "hidden" && (
        <div style={{ position:"absolute", inset:0, background:"rgba(0,0,0,0.45)",
                      display:"flex", alignItems:"center", justifyContent:"center", zIndex:50 }}>
          <div style={{ background:"#fff", borderRadius:16, padding:"1.75rem",
                        maxWidth:360, width:"90%", boxShadow:"0 8px 40px rgba(0,0,0,0.18)" }}>
            {deleteModal === "denied" ? (
              <>
                <div style={{ fontSize:"1.4rem", marginBottom:"0.5rem" }}>🔒</div>
                <div style={{ fontWeight:700, fontSize:"1rem", color:"#EF4444", marginBottom:"0.4rem" }}>
                  Access Denied
                </div>
                <p style={{ fontSize:"0.85rem", color:"#555", marginBottom:"1.25rem", lineHeight:1.6 }}>
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
                <div style={{ fontSize:"1.4rem", marginBottom:"0.5rem" }}>🗑</div>
                <div style={{ fontWeight:700, fontSize:"1rem", marginBottom:"0.4rem" }}>
                  Delete Slide {current + 1}?
                </div>
                <p style={{ fontSize:"0.85rem", color:"#555", marginBottom:"1.25rem", lineHeight:1.6 }}>
                  You are about to delete <strong>"{slideTitle}"</strong>. This action cannot be undone.
                </p>
                <div style={{ display:"flex", gap:"0.75rem" }}>
                  <button onClick={() => setDeleteModal("hidden")}
                    style={{ flex:1, padding:"0.6rem", borderRadius:8,
                             border:"1.5px solid #E5E2DA", background:"#fff",
                             cursor:"pointer", fontSize:"0.88rem" }}>
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
