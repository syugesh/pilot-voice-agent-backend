"""
Builds generated presentations by cloning slides out of the real Grid
Dynamics branded template instead of drawing shapes from scratch.

The template (data/ppt_templates/GD_template/GD Presentation template.pptx)
is a Google Slides export: almost every slide has its own dedicated
layout with generically-named shapes, so the normal python-pptx
add_slide(layout) + fill-placeholders workflow doesn't apply. Instead we
clone an existing example slide (which already carries the exact
background/branding for that slide "kind") and swap its placeholder text
for generated content, matched by the template's known placeholder text
rather than by shape id/order (ids aren't stable across clones).
"""
import asyncio, contextvars, copy, datetime, json, logging, random, re
from pptx import Presentation
from pptx.oxml.ns import qn
from lxml import etree

logger = logging.getLogger("pilot.ppt_template_builder")

TEMPLATE_PATH = "data/ppt_templates/GD_template/GD Presentation template.pptx"
TEMPLATE_PATH_MINIMALIST = "data/ppt_templates/GD_template/Latest - GD Presentation Template – Minimalist Style.pptx"


# ── Slide duplication / deletion — python-pptx has no public API for either ──

def _duplicate_slide(prs, source_index: int):
    """Clone prs.slides[source_index] (background, all shapes, tables, images)
    onto a freshly appended slide sharing the same layout."""
    source = prs.slides[source_index]
    new_slide = prs.slides.add_slide(source.slide_layout)

    # add_slide() pre-populates the new slide with empty placeholder shapes
    # inherited from the layout ("Click to add title", etc), and `.shapes`
    # caches a reference to that exact spTree element object. Swapping in a
    # whole new cSld/spTree (different object identity) would leave the
    # cached `slide.shapes` accessor pointing at the orphaned original — so
    # mutate the existing spTree's children in place instead.
    new_cSld = new_slide._element.find(qn("p:cSld"))
    src_cSld = source._element.find(qn("p:cSld"))
    new_spTree = new_cSld.find(qn("p:spTree"))
    src_spTree = src_cSld.find(qn("p:spTree"))
    _BOILERPLATE = (qn("p:nvGrpSpPr"), qn("p:grpSpPr"))

    src_bg = src_cSld.find(qn("p:bg"))
    existing_bg = new_cSld.find(qn("p:bg"))
    if existing_bg is not None:
        new_cSld.remove(existing_bg)
    if src_bg is not None:
        new_cSld.insert(0, copy.deepcopy(src_bg))

    for child in list(new_spTree):
        if child.tag not in _BOILERPLATE:
            new_spTree.remove(child)
    for shape_el in src_spTree:
        if shape_el.tag not in _BOILERPLATE:
            new_spTree.append(copy.deepcopy(shape_el))

    # r:embed / r:link attrs in the copied XML still reference the SOURCE
    # slide's relationship ids, which don't exist in the new slide's part.
    # Re-point each to a fresh relationship in the new part targeting the
    # SAME image part — shares bytes instead of duplicating media.
    old_to_new: dict[str, str] = {}
    for el in new_slide._element.iter():
        for attr in ("embed", "link"):
            rid = el.get(qn(f"r:{attr}"))
            if not rid:
                continue
            if rid not in old_to_new:
                rel = source.part.rels[rid]
                old_to_new[rid] = new_slide.part.relate_to(rel.target_part, rel.reltype)
            el.set(qn(f"r:{attr}"), old_to_new[rid])

    return new_slide


def _delete_slide_at(prs, index: int):
    sld_id_lst = prs.slides._sldIdLst
    sld_id_element = list(sld_id_lst)[index]
    # The <p:sldId> element's r:id attribute is the relationship the
    # presentation part uses to reach this slide's part. Removing only the
    # sldId (and not the relationship) leaves it dangling — the slide part
    # is still reachable via presentation.xml.rels even though nothing in
    # sldIdLst references it anymore. That inconsistency doesn't break the
    # very next save (this file's own build), but corrupts the package
    # (duplicate zip entries) the NEXT time it's reopened and any part of it
    # is touched and re-saved — i.e. exactly what every future voice/UI edit
    # does. Drop the relationship too so the deleted slide's part becomes a
    # true, fully-unreferenced orphan.
    r_id = sld_id_element.get(qn("r:id"))
    sld_id_lst.remove(sld_id_element)
    if r_id:
        prs.part.drop_rel(r_id)


def _strip_original_slides(prs, original_count: int):
    """New clones are always appended after the template's own slides, so the
    first `original_count` slides are always the scaffold — drop them from
    the front, `original_count` times, after all cloning is done."""
    for _ in range(original_count):
        _delete_slide_at(prs, 0)


def _copy_slide_from_external(target_prs, source_prs, source_index: int):
    """Like _duplicate_slide, but source_prs and target_prs are DIFFERENT,
    already-open Presentation objects — used to pull a kind-slide from the
    pristine GD template into an already-saved deck that doesn't yet contain
    an example of that kind. Cross-package copying needs two things
    _duplicate_slide doesn't: a "carrier" layout already in the target
    package (python-pptx's add_slide() requires the layout argument to
    belong to the same package), and real image parts registered in the
    target package for every r:embed/r:link — re-pointing to the source's
    relationship id (as the same-document version does) would reference a
    part that doesn't exist in the target package at all.
    """
    source = source_prs.slides[source_index]
    # Any existing layout works — every visible thing (background + every
    # shape) is overwritten below with a deep copy of the source slide, so
    # the layout only satisfies add_slide()'s required argument.
    carrier_layout = target_prs.slide_layouts[0]
    new_slide = target_prs.slides.add_slide(carrier_layout)

    new_cSld = new_slide._element.find(qn("p:cSld"))
    src_cSld = source._element.find(qn("p:cSld"))
    new_spTree = new_cSld.find(qn("p:spTree"))
    src_spTree = src_cSld.find(qn("p:spTree"))
    _BOILERPLATE = (qn("p:nvGrpSpPr"), qn("p:grpSpPr"))

    src_bg = src_cSld.find(qn("p:bg"))
    existing_bg = new_cSld.find(qn("p:bg"))
    if existing_bg is not None:
        new_cSld.remove(existing_bg)
    if src_bg is not None:
        new_cSld.insert(0, copy.deepcopy(src_bg))

    for child in list(new_spTree):
        if child.tag not in _BOILERPLATE:
            new_spTree.remove(child)
    for shape_el in src_spTree:
        if shape_el.tag not in _BOILERPLATE:
            new_spTree.append(copy.deepcopy(shape_el))

    from io import BytesIO
    old_to_new: dict[str, str] = {}
    for el in new_slide._element.iter():
        for attr in ("embed", "link"):
            rid = el.get(qn(f"r:{attr}"))
            if not rid:
                continue
            if rid not in old_to_new:
                rel = source.part.rels[rid]
                # get_or_add_image_part dedupes by content hash, so copying
                # the same GD background/logo across multiple add-slide calls
                # doesn't create duplicate image parts.
                _, new_rid = new_slide.part.get_or_add_image_part(BytesIO(rel.target_part.blob))
                old_to_new[rid] = new_rid
            el.set(qn(f"r:{attr}"), old_to_new[rid])

    return new_slide


def _move_slide(prs, old_index: int, new_index: int):
    """Reorder a slide within sldIdLst — python-pptx has no public API for this."""
    sld_id_lst = prs.slides._sldIdLst
    slides = list(sld_id_lst)
    el = slides[old_index]
    sld_id_lst.remove(el)
    sld_id_lst.insert(new_index, el)


def add_slide_to_deck(pptx_path: str, kind: str, data: dict,
                       source_index_in_deck: int | None = None,
                       existing_source: str | None = None,
                       insert_after: int | None = None) -> tuple[int, str | None]:
    """Insert one new populated slide of `kind` into an ALREADY-SAVED
    presentation (as opposed to build_deck_from_template, which always
    builds a fresh deck from scratch). If the caller already knows this deck
    contains a slide of the same kind (source_index_in_deck, looked up via
    the kinds sidecar in api/ppt.py), that slide is cloned directly — the
    same well-tested same-document path used during initial generation, and
    it inherits that slide's recorded `existing_source` (its clone shares
    the same shape_ids, so the same slot map applies). Otherwise a random
    candidate is pulled fresh from the template pool (see _KIND_SOURCES) so
    repeated "add a slide" calls don't all draw the same layout either.
    Returns (new slide's final 0-indexed position, its source string).
    """
    if kind not in _KIND_SOURCES or kind not in _POPULATE:
        raise ValueError(f"Unknown slide kind: {kind!r}")

    prs = Presentation(pptx_path)

    if source_index_in_deck is not None:
        new_slide = _duplicate_slide(prs, source_index_in_deck)
        source = existing_source
    else:
        source_path, source_index = pick_source(kind)
        new_slide = _copy_slide_from_external(prs, _get_template_prs(source_path), source_index)
        source = f"{source_path}::{source_index}"

    try:
        populate_slide_data(new_slide, kind, data, _parse_source(source))
    except Exception as e:
        logger.error(f"Failed to populate new {kind!r} slide: {e}", exc_info=True)

    new_index = len(prs.slides) - 1  # add_slide() always appends
    if insert_after is not None:
        target_index = min(insert_after + 1, new_index)
        if target_index != new_index:
            _move_slide(prs, new_index, target_index)
            new_index = target_index

    prs.save(pptx_path)
    logger.info(f"Added {kind!r} slide at index {new_index} (source={source}) → {pptx_path}")
    return new_index, source


def _parse_source(source: str | None) -> tuple[str, int] | None:
    if not source:
        return None
    path, _, idx = source.rpartition("::")
    return (path, int(idx)) if path else None


# ── Text helpers — locate + rewrite shapes by the template's known text ──────

def _first_para_text(shape) -> str:
    """Text of a shape's first paragraph only — not the whole text_frame,
    which joins every paragraph. Several template shapes hold more than one
    paragraph (e.g. a "Name Surname" / "Title" pair in one textbox, or an
    agenda body with 7 "Text" paragraphs), and matching against the marker
    text must key off the first line, not the concatenation of all of them."""
    if not shape.has_text_frame:
        return ""
    paras = shape.text_frame.paragraphs
    return paras[0].text.strip() if paras else ""


def _find_shapes(slide, marker: str, *, exact: bool = False):
    matches = []
    for sh in slide.shapes:
        if not sh.has_text_frame:
            continue
        text = _first_para_text(sh)
        if (text == marker) if exact else text.startswith(marker):
            matches.append(sh)
    return matches


def _find_shape(slide, marker: str, *, exact: bool = False, nth: int = 0):
    matches = _find_shapes(slide, marker, exact=exact)
    return matches[nth] if len(matches) > nth else None


def _find_shape_by_type(slide, ph_type_name: str):
    from pptx.enum.shapes import PP_PLACEHOLDER_TYPE as PPT
    for sh in slide.shapes:
        if not sh.is_placeholder:
            continue
        try:
            if sh.placeholder_format.type is not None and str(sh.placeholder_format.type).startswith(ph_type_name):
                return sh
        except Exception:
            continue
    return None


def _set_paragraphs(text_frame_owner, texts: list[str]):
    """Replace all paragraphs in a shape/cell's text_frame with `texts`.

    Each output line reuses the ORIGINAL paragraph at the same index as its
    formatting template (falling back to the last original paragraph once
    `texts` runs longer than the template had) — not just paragraph 0 for
    everything. Several template shapes hold paragraphs with genuinely
    different per-line formatting (e.g. a bold "Name Surname" line followed
    by a smaller, non-bold "Title" line in the same textbox); cloning only
    paragraph 0 for both would make the role/title line render in the name's
    bold style instead of its own.
    """
    tf = text_frame_owner.text_frame
    txBody = tf._txBody
    xml_paras = txBody.findall(qn("a:p"))
    if not xml_paras:
        return
    templates = [copy.deepcopy(p) for p in xml_paras]
    for p in xml_paras:
        txBody.remove(p)

    for i, text in enumerate(texts or [""]):
        template = templates[i] if i < len(templates) else templates[-1]
        new_p = copy.deepcopy(template)
        runs = new_p.findall(qn("a:r"))
        if runs:
            first_run = runs[0]
            t_el = first_run.find(qn("a:t"))
            if t_el is None:
                t_el = etree.SubElement(first_run, qn("a:t"))
            t_el.text = text
            for extra in runs[1:]:
                new_p.remove(extra)
        else:
            r = etree.SubElement(new_p, qn("a:r"))
            t_el = etree.SubElement(r, qn("a:t"))
            t_el.text = text
        txBody.append(new_p)


def _set_text(shape, text: str):
    _set_paragraphs(shape, [text])


def _resize_table(table, n_rows: int):
    tbl = table._tbl
    rows = tbl.findall(qn("a:tr"))
    if not rows:
        return
    if n_rows < len(rows):
        for r in rows[n_rows:]:
            tbl.remove(r)
    elif n_rows > len(rows):
        template_row = copy.deepcopy(rows[-1])
        for _ in range(n_rows - len(rows)):
            tbl.append(copy.deepcopy(template_row))


# ── Slot maps — shape_id is a stable key across every clone of a kind ────────
#
# _duplicate_slide deep-copies each source shape's XML verbatim, including
# its <p:cNvPr id="..."> — so every clone of a given kind ends up with the
# exact same shape_ids the pristine template's example slide has. That means
# the marker-text matching below (find the shape whose text starts with
# "Lorem Ipsum", etc.) only needs to run ONCE, against the pristine
# template, to build a {slot_name: shape_id} map per kind. The same map then
# drives both populate (write new content in by shape_id) and extract (read
# current content back out by shape_id, for the edit UI/voice tool) — no
# fragile re-matching against already-overwritten text.

GD, MIN = TEMPLATE_PATH, TEMPLATE_PATH_MINIMALIST

# Every candidate source slide for each kind, verified by actually running
# that kind's locator against every slide in both template files and
# checking every expected shape resolved (see _verify_kind_sources at the
# bottom of this file) — not just "looks similar," since a locator that
# only grabs the FIRST of several same-named marker shapes (e.g. "text"
# matching only one of a two-column slide's two "Lorem Ipsum" bodies) would
# silently leave the other one showing raw dummy template copy. A kind with
# only one entry here had only one genuinely safe candidate; the rest were
# excluded for that reason, not overlooked.
_KIND_SOURCES: dict[str, list[tuple[str, int]]] = {
    "cover":        [(GD,0),(GD,1),(GD,2),(GD,3),(GD,4),(GD,5),(MIN,0),(MIN,1)],
    "agenda":       [(GD,6),(MIN,4)],
    "text":         [(GD,7),(GD,8),(GD,30)],
    "two_column":   [(GD,9),(GD,10)],
    "comparison":   [(GD,9),(GD,10)],
    "team":         [(GD,11),(GD,12),(GD,13),(MIN,3)],
    "speaker_1":    [(GD,12),(GD,13)],
    "speaker_4":    [(GD,13)],
    "text_blocks":  [(GD,14)],
    "key_message":  [(GD,19)],
    "table":        [(GD,23),(GD,33),(GD,34),(GD,35)],
    "subsection":   [(GD,24),(GD,25),(GD,27),(GD,28),(MIN,9)],
    "chapter":      [(GD,29)],
    "thank_you":    [(GD,38),(MIN,11)],
}

# Legacy fixed index per kind — the source every already-generated deck was
# actually built with, before per-slide source tracking existed. Used as the
# fallback when editing/extracting a slide whose sidecar has no recorded
# source (see api/ppt.py's kinds vs sources sidecars).
_KIND_SOURCE_INDEX: dict[str, int] = {
    "cover": 0, "agenda": 6, "text": 7, "two_column": 9, "comparison": 10,
    "team": 11, "speaker_1": 12, "speaker_4": 13, "text_blocks": 14,
    "key_message": 19, "table": 23, "subsection": 24, "chapter": 29, "thank_you": 38,
}


def pick_source(kind: str) -> tuple[str, int]:
    return random.choice(_KIND_SOURCES[kind])


def _sid(shape):
    return shape.shape_id if shape else None


def _locate_cover(slide) -> dict:
    return {
        "title": _sid(_find_shape(slide, "Name of presentation", exact=False)),
        "date":  _sid(_find_shape(slide, "Month Year", exact=True)),
    }


def _locate_agenda(slide) -> dict:
    return {
        "title": _sid(_find_shape_by_type(slide, "TITLE")),
        "items": _sid(_find_shape(slide, "Text", exact=True)),
    }


def _locate_text(slide) -> dict:
    return {
        "title":      _sid(_find_shape_by_type(slide, "TITLE")),
        "paragraphs": _sid(_find_shape(slide, "Lorem Ipsum")),
    }


def _locate_two_col(slide) -> dict:
    headings = sorted(_find_shapes(slide, "Title", exact=True), key=lambda s: s.left)
    bodies = sorted(_find_shapes(slide, "Lorem Ipsum"), key=lambda s: s.left)
    return {
        "title": _sid(_find_shape_by_type(slide, "TITLE")),
        "left":  {"heading": _sid(headings[0] if len(headings) > 0 else None), "body": _sid(bodies[0] if len(bodies) > 0 else None)},
        "right": {"heading": _sid(headings[1] if len(headings) > 1 else None), "body": _sid(bodies[1] if len(bodies) > 1 else None)},
    }


def _locate_team(slide) -> dict:
    pairs = sorted(_find_shapes(slide, "Name Surname", exact=True), key=lambda s: (round(s.top, -4), s.left))
    return {
        "title":  _sid(_find_shape_by_type(slide, "TITLE")),
        "people": [_sid(s) for s in pairs],
    }


def _locate_speaker_1(slide) -> dict:
    return {
        "title": _sid(_find_shape_by_type(slide, "TITLE")),
        "name":  _sid(_find_shape(slide, "Name Surname", exact=True)),
        "role":  _sid(_find_shape(slide, "Title", exact=True)),
        "bio":   _sid(_find_shape(slide, "Praesent vitae")),
    }


def _locate_speaker_4(slide) -> dict:
    names = sorted(_find_shapes(slide, "Name Surname", exact=True), key=lambda s: (round(s.top, -4), s.left))
    roles = sorted(_find_shapes(slide, "Title", exact=True), key=lambda s: (round(s.top, -4), s.left))
    bios  = sorted(_find_shapes(slide, "Praesent vitae"), key=lambda s: (round(s.top, -4), s.left))
    return {
        "title": _sid(_find_shape_by_type(slide, "TITLE")),
        "speakers": [
            {"name": _sid(names[i] if i < len(names) else None),
             "role": _sid(roles[i] if i < len(roles) else None),
             "bio":  _sid(bios[i] if i < len(bios) else None)}
            for i in range(4)
        ],
    }


def _locate_text_blocks(slide) -> dict:
    headings = sorted(_find_shapes(slide, "Lower cost of solution maintenance", exact=True), key=lambda s: (round(s.top, -4), s.left))
    bodies   = sorted(_find_shapes(slide, "Predictable low cost"), key=lambda s: (round(s.top, -4), s.left))
    return {
        "title":  _sid(_find_shape_by_type(slide, "TITLE")),
        "blocks": [
            {"heading": _sid(headings[i]), "items": _sid(bodies[i])}
            for i in range(min(len(headings), len(bodies)))
        ],
    }


def _locate_key_message(slide) -> dict:
    bodies = sorted(_find_shapes(slide, "You can place icons"), key=lambda s: s.top)
    return {
        "title":    _sid(_find_shape_by_type(slide, "TITLE")),
        "messages": [_sid(s) for s in bodies],
    }


def _locate_table(slide) -> dict:
    table_shape = next((sh for sh in slide.shapes if sh.has_table), None)
    return {
        "title": _sid(_find_shape_by_type(slide, "TITLE")),
        "table": _sid(table_shape),
    }


def _locate_subsection(slide) -> dict:
    return {"title": _sid(_find_shape_by_type(slide, "TITLE"))}


def _locate_chapter(slide) -> dict:
    return {
        "title":       _sid(_find_shape_by_type(slide, "TITLE")),
        "description": _sid(_find_shape(slide, "This is a brief description")),
    }


def _locate_thank_you(slide) -> dict:
    return {}  # static slide, real text lives on the shared layout — nothing to locate


_LOCATORS: dict[str, callable] = {
    "cover":        _locate_cover,
    "agenda":       _locate_agenda,
    "text":         _locate_text,
    "two_column":   _locate_two_col,
    "comparison":   _locate_two_col,
    "team":         _locate_team,
    "speaker_1":    _locate_speaker_1,
    "speaker_4":    _locate_speaker_4,
    "text_blocks":  _locate_text_blocks,
    "key_message":  _locate_key_message,
    "table":        _locate_table,
    "subsection":   _locate_subsection,
    "chapter":      _locate_chapter,
    "thank_you":    _locate_thank_you,
}

_SLOT_MAP_CACHE: dict[tuple, dict] = {}
_TEMPLATE_PRS_CACHE: dict[str, "Presentation"] = {}

# Which (template_path, index) a populate/extract call should read its slot
# map from — set by the four entry points below (build_deck_from_template,
# add_slide_to_deck, populate_slide_data, extract_slide_data) right before
# dispatching into _POPULATE[kind]/_EXTRACT[kind]. Reading it inside
# get_slot_map (rather than threading a `source` parameter through all 28
# _populate_*/_extract_* functions) means none of those functions need to
# change at all — they already just call get_slot_map(kind) as before.
# ContextVar (not a plain module global) so concurrent requests running in
# different asyncio.to_thread workers never see each other's source.
_current_source: contextvars.ContextVar[tuple | None] = contextvars.ContextVar("current_source", default=None)


def _get_template_prs(template_path: str):
    if template_path not in _TEMPLATE_PRS_CACHE:
        _TEMPLATE_PRS_CACHE[template_path] = Presentation(template_path)
    return _TEMPLATE_PRS_CACHE[template_path]


def get_slot_map(kind: str) -> dict:
    source = _current_source.get() or (TEMPLATE_PATH, _KIND_SOURCE_INDEX[kind])
    cache_key = (kind, source)
    if cache_key not in _SLOT_MAP_CACHE:
        template_path, index = source
        prs = _get_template_prs(template_path)
        slide = prs.slides[index]
        _SLOT_MAP_CACHE[cache_key] = _LOCATORS[kind](slide)
    return _SLOT_MAP_CACHE[cache_key]


def _shape_by_id(slide, shape_id):
    if shape_id is None:
        return None
    for sh in slide.shapes:
        if sh.shape_id == shape_id:
            return sh
    return None


def _para_text(shape, idx: int = 0) -> str:
    if not shape or not shape.has_text_frame:
        return ""
    paras = shape.text_frame.paragraphs
    return paras[idx].text.strip() if idx < len(paras) else ""


# ── Populate — write generated content into a freshly cloned slide ───────────

def _populate_cover(slide, data: dict):
    from pptx.util import Pt

    slots = get_slot_map("cover")
    title = _shape_by_id(slide, slots["title"])
    if title:
        title_text = data.get("title", "Presentation")
        _set_paragraphs(title, [title_text, "Grid Dynamics"])
        # The title box has almost no vertical slack below its default 2-line
        # content (its bottom edge sits right at the date box's top) — an
        # AI-generated title longer than the template's short placeholder
        # text wraps to 3+ lines at the default 24pt and overlaps the date
        # below it. Shrink proportionally to the title's length as a backstop
        # (the prompt also asks the model to keep titles short).
        if len(title_text) > 30:
            size = Pt(18) if len(title_text) > 55 else Pt(20)
            for run in title.text_frame.paragraphs[0].runs:
                run.font.size = size
    date = _shape_by_id(slide, slots["date"])
    if date:
        _set_text(date, data.get("date", ""))


def _populate_agenda(slide, data: dict):
    slots = get_slot_map("agenda")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", "Agenda"))
    body = _shape_by_id(slide, slots["items"])
    if body:
        _set_paragraphs(body, data.get("items") or ["Text"])


def _populate_text(slide, data: dict):
    slots = get_slot_map("text")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", ""))
    body = _shape_by_id(slide, slots["paragraphs"])
    if body:
        _set_paragraphs(body, data.get("paragraphs") or [""])


def _populate_two_col_shape(slide, data: dict, kind: str):
    slots = get_slot_map(kind)
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", ""))
    for side in ("left", "right"):
        col = data.get(side) or {}
        heading = _shape_by_id(slide, slots[side]["heading"])
        body = _shape_by_id(slide, slots[side]["body"])
        if heading:
            _set_text(heading, col.get("heading", ""))
        if body:
            _set_paragraphs(body, [col.get("body", "")])


def _populate_two_column(slide, data: dict):
    _populate_two_col_shape(slide, data, "two_column")


def _populate_comparison(slide, data: dict):
    _populate_two_col_shape(slide, data, "comparison")


def _populate_team(slide, data: dict):
    slots = get_slot_map("team")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", "Your Grid Dynamics Team Today"))
    people = data.get("people") or []
    for i, shape_id in enumerate(slots["people"]):
        shape = _shape_by_id(slide, shape_id)
        if not shape:
            continue
        if i < len(people):
            _set_paragraphs(shape, [people[i].get("name", ""), people[i].get("title", "")])
        else:
            _set_paragraphs(shape, ["", ""])  # clear unused slots instead of leaving template placeholder text


def _populate_speaker_1(slide, data: dict):
    slots = get_slot_map("speaker_1")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", "This is a slide with 1 speaker"))
    name = _shape_by_id(slide, slots["name"])
    if name:
        _set_text(name, data.get("name", ""))
    role = _shape_by_id(slide, slots["role"])
    if role:
        _set_text(role, data.get("role", ""))
    bio = _shape_by_id(slide, slots["bio"])
    if bio:
        _set_paragraphs(bio, [data.get("bio", "")])


def _populate_speaker_4(slide, data: dict):
    slots = get_slot_map("speaker_4")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", "This is a slide with 4 speakers"))
    speakers = data.get("speakers") or []
    for i, slot in enumerate(slots["speakers"]):
        sp = speakers[i] if i < len(speakers) else {}
        name_shape = _shape_by_id(slide, slot["name"])
        role_shape = _shape_by_id(slide, slot["role"])
        bio_shape  = _shape_by_id(slide, slot["bio"])
        if name_shape:
            _set_text(name_shape, sp.get("name", ""))
        if role_shape:
            _set_text(role_shape, sp.get("role", ""))
        if bio_shape:
            _set_paragraphs(bio_shape, [sp.get("bio", "")])


def _populate_text_blocks(slide, data: dict):
    slots = get_slot_map("text_blocks")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", ""))
    blocks = data.get("blocks") or []
    for i, slot in enumerate(slots["blocks"]):
        block = blocks[i] if i < len(blocks) else {}
        heading_shape = _shape_by_id(slide, slot["heading"])
        items_shape = _shape_by_id(slide, slot["items"])
        if heading_shape:
            _set_text(heading_shape, block.get("heading", ""))
        if items_shape:
            _set_paragraphs(items_shape, block.get("items") or [""])


def _populate_table(slide, data: dict):
    slots = get_slot_map("table")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", "Slide with table"))
    table_shape = _shape_by_id(slide, slots["table"])
    if not table_shape or not table_shape.has_table:
        return
    table = table_shape.table
    n_cols = len(table.columns)
    rows = data.get("rows") or []
    _resize_table(table, 1 + len(rows))

    # Pad headers/rows out to the template's full column count with blanks —
    # otherwise any column the model didn't provide keeps the template's
    # original placeholder text ("Section 2", "14.4%", ...) instead of being
    # cleared, since only the columns we explicitly touch get overwritten.
    headers = ((data.get("headers") or [])[:n_cols] + [""] * n_cols)[:n_cols]
    for c in range(n_cols):
        _set_paragraphs(table.cell(0, c), [headers[c]])
    for r, row in enumerate(rows, start=1):
        padded = (list(row)[:n_cols] + [""] * n_cols)[:n_cols]
        for c in range(n_cols):
            _set_paragraphs(table.cell(r, c), [str(padded[c])])


def _populate_subsection(slide, data: dict):
    slots = get_slot_map("subsection")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", ""))


def _populate_chapter(slide, data: dict):
    slots = get_slot_map("chapter")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", ""))
    desc = _shape_by_id(slide, slots["description"])
    if desc:
        _set_text(desc, data.get("description", ""))


def _populate_key_message(slide, data: dict):
    slots = get_slot_map("key_message")
    title = _shape_by_id(slide, slots["title"])
    if title:
        _set_text(title, data.get("title", "Key message"))
    messages = data.get("messages") or []
    for i, shape_id in enumerate(slots["messages"]):
        shape = _shape_by_id(slide, shape_id)
        if shape:
            _set_text(shape, messages[i] if i < len(messages) else "")


def _populate_thank_you(slide, data: dict):
    pass  # static slide, real text lives on the shared layout — nothing to fill


_POPULATE: dict[str, callable] = {
    "cover":        _populate_cover,
    "agenda":       _populate_agenda,
    "text":         _populate_text,
    "two_column":   _populate_two_column,
    "comparison":   _populate_comparison,
    "team":         _populate_team,
    "speaker_1":    _populate_speaker_1,
    "speaker_4":    _populate_speaker_4,
    "text_blocks":  _populate_text_blocks,
    "key_message":  _populate_key_message,
    "table":        _populate_table,
    "subsection":   _populate_subsection,
    "chapter":      _populate_chapter,
    "thank_you":    _populate_thank_you,
}


# ── Extract — read a populated slide's current content back out, by the
# same shape_ids populate used, for the edit UI and voice-edit tool ─────────

def _extract_cover(slide) -> dict:
    slots = get_slot_map("cover")
    title = _shape_by_id(slide, slots["title"])
    return {"title": _para_text(title, 0), "date": _para_text(_shape_by_id(slide, slots["date"]))}


def _extract_agenda(slide) -> dict:
    slots = get_slot_map("agenda")
    body = _shape_by_id(slide, slots["items"])
    items = [p.text.strip() for p in body.text_frame.paragraphs] if body else []
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "items": [i for i in items if i]}


def _extract_text(slide) -> dict:
    slots = get_slot_map("text")
    body = _shape_by_id(slide, slots["paragraphs"])
    paragraphs = [p.text.strip() for p in body.text_frame.paragraphs] if body else []
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "paragraphs": [p for p in paragraphs if p]}


def _extract_two_col_shape(slide, kind: str) -> dict:
    slots = get_slot_map(kind)
    out = {"title": _para_text(_shape_by_id(slide, slots["title"]))}
    for side in ("left", "right"):
        out[side] = {
            "heading": _para_text(_shape_by_id(slide, slots[side]["heading"])),
            "body":    _para_text(_shape_by_id(slide, slots[side]["body"])),
        }
    return out


def _extract_two_column(slide) -> dict:
    return _extract_two_col_shape(slide, "two_column")


def _extract_comparison(slide) -> dict:
    return _extract_two_col_shape(slide, "comparison")


def _extract_team(slide) -> dict:
    slots = get_slot_map("team")
    people = []
    for shape_id in slots["people"]:
        shape = _shape_by_id(slide, shape_id)
        name = _para_text(shape, 0)
        if name:
            people.append({"name": name, "title": _para_text(shape, 1)})
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "people": people}


def _extract_speaker_1(slide) -> dict:
    slots = get_slot_map("speaker_1")
    return {
        "title": _para_text(_shape_by_id(slide, slots["title"])),
        "name":  _para_text(_shape_by_id(slide, slots["name"])),
        "role":  _para_text(_shape_by_id(slide, slots["role"])),
        "bio":   _para_text(_shape_by_id(slide, slots["bio"])),
    }


def _extract_speaker_4(slide) -> dict:
    slots = get_slot_map("speaker_4")
    speakers = []
    for slot in slots["speakers"]:
        name = _para_text(_shape_by_id(slide, slot["name"]))
        if name:
            speakers.append({
                "name": name,
                "role": _para_text(_shape_by_id(slide, slot["role"])),
                "bio":  _para_text(_shape_by_id(slide, slot["bio"])),
            })
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "speakers": speakers}


def _extract_text_blocks(slide) -> dict:
    slots = get_slot_map("text_blocks")
    blocks = []
    for slot in slots["blocks"]:
        heading = _para_text(_shape_by_id(slide, slot["heading"]))
        items_shape = _shape_by_id(slide, slot["items"])
        items = [p.text.strip() for p in items_shape.text_frame.paragraphs] if items_shape else []
        items = [i for i in items if i]
        if heading or items:
            blocks.append({"heading": heading, "items": items})
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "blocks": blocks}


def _extract_key_message(slide) -> dict:
    slots = get_slot_map("key_message")
    messages = [_para_text(_shape_by_id(slide, sid)) for sid in slots["messages"]]
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "messages": [m for m in messages if m]}


def _extract_table(slide) -> dict:
    slots = get_slot_map("table")
    table_shape = _shape_by_id(slide, slots["table"])
    if not table_shape or not table_shape.has_table:
        return {"title": _para_text(_shape_by_id(slide, slots["title"])), "headers": [], "rows": []}
    table = table_shape.table
    n_rows, n_cols = len(table.rows), len(table.columns)
    headers = [table.cell(0, c).text.strip() for c in range(n_cols)] if n_rows > 0 else []
    rows = [[table.cell(r, c).text.strip() for c in range(n_cols)] for r in range(1, n_rows)]
    return {"title": _para_text(_shape_by_id(slide, slots["title"])), "headers": headers, "rows": rows}


def _extract_subsection(slide) -> dict:
    slots = get_slot_map("subsection")
    return {"title": _para_text(_shape_by_id(slide, slots["title"]))}


def _extract_chapter(slide) -> dict:
    slots = get_slot_map("chapter")
    return {
        "title": _para_text(_shape_by_id(slide, slots["title"])),
        "description": _para_text(_shape_by_id(slide, slots["description"])),
    }


def _extract_thank_you(slide) -> dict:
    return {}


_EXTRACT: dict[str, callable] = {
    "cover":        _extract_cover,
    "agenda":       _extract_agenda,
    "text":         _extract_text,
    "two_column":   _extract_two_column,
    "comparison":   _extract_comparison,
    "team":         _extract_team,
    "speaker_1":    _extract_speaker_1,
    "speaker_4":    _extract_speaker_4,
    "text_blocks":  _extract_text_blocks,
    "key_message":  _extract_key_message,
    "table":        _extract_table,
    "subsection":   _extract_subsection,
    "chapter":      _extract_chapter,
    "thank_you":    _extract_thank_you,
}


# ── Field schema — declarative description of each kind's editable fields,
# consumed by the frontend to render a generic edit form without needing a
# bespoke layout per kind ─────────────────────────────────────────────────

_COLUMN_FIELD = {
    "type": "object",
    "fields": [
        {"key": "heading", "type": "text", "label": "Heading"},
        {"key": "body", "type": "textarea", "label": "Body"},
    ],
}

KIND_FIELD_SCHEMA: dict[str, list[dict]] = {
    "cover": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "date", "type": "text", "label": "Date"},
    ],
    "agenda": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "items", "type": "list_of_strings", "label": "Agenda Items"},
    ],
    "text": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "paragraphs", "type": "list_of_strings", "label": "Paragraphs", "textarea": True},
    ],
    "two_column": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "left", "label": "Left Column", **_COLUMN_FIELD},
        {"key": "right", "label": "Right Column", **_COLUMN_FIELD},
    ],
    "comparison": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "left", "label": "Left Column", **_COLUMN_FIELD},
        {"key": "right", "label": "Right Column", **_COLUMN_FIELD},
    ],
    "team": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "people", "type": "list_of_objects", "label": "Team Members", "item_fields": [
            {"key": "name", "type": "text", "label": "Name"},
            {"key": "title", "type": "text", "label": "Role"},
        ]},
    ],
    "speaker_1": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "name", "type": "text", "label": "Name"},
        {"key": "role", "type": "text", "label": "Role"},
        {"key": "bio", "type": "textarea", "label": "Bio"},
    ],
    "speaker_4": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "speakers", "type": "list_of_objects", "label": "Speakers", "fixed_length": 4, "item_fields": [
            {"key": "name", "type": "text", "label": "Name"},
            {"key": "role", "type": "text", "label": "Role"},
            {"key": "bio", "type": "textarea", "label": "Bio"},
        ]},
    ],
    "text_blocks": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "blocks", "type": "list_of_objects", "label": "Blocks", "max_length": 4, "item_fields": [
            {"key": "heading", "type": "text", "label": "Heading"},
            {"key": "items", "type": "list_of_strings", "label": "Items"},
        ]},
    ],
    "key_message": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "messages", "type": "list_of_strings", "label": "Messages", "max_length": 2},
    ],
    "table": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "table", "type": "table_grid", "label": "Table", "max_cols": 5},
    ],
    "subsection": [
        {"key": "title", "type": "text", "label": "Title"},
    ],
    "chapter": [
        {"key": "title", "type": "text", "label": "Title"},
        {"key": "description", "type": "textarea", "label": "Description"},
    ],
    "thank_you": [],
}


def extract_slide_data(slide, kind: str, source: tuple[str, int] | None = None) -> dict:
    fn = _EXTRACT.get(kind)
    if not fn:
        return {}
    token = _current_source.set(source)
    try:
        return fn(slide)
    finally:
        _current_source.reset(token)


def populate_slide_data(slide, kind: str, data: dict, source: tuple[str, int] | None = None):
    fn = _POPULATE.get(kind)
    if not fn:
        return
    token = _current_source.set(source)
    try:
        fn(slide, data)
    finally:
        _current_source.reset(token)


def build_deck_from_template(content: dict, out_path: str, template_path: str = TEMPLATE_PATH) -> list[str | None]:
    """Builds the deck and returns the per-slide source used (as
    "path::index" strings, same order as the final deck) so the caller can
    persist it — a later edit needs to know exactly which candidate slide a
    given kind was cloned from to compute the matching slot map, since
    different candidates for the same kind don't share shape_ids.
    """
    # A fresh copy to mutate — _get_template_prs's cached Presentation
    # objects are reused as cross-copy SOURCES for every generation call
    # and must stay pristine.
    prs = Presentation(template_path)
    original_count = len(prs.slides)
    sources_used: list[str | None] = []

    for slide_data in content.get("slides", []):
        kind = slide_data.get("kind")
        if kind not in _KIND_SOURCES or kind not in _POPULATE:
            logger.warning(f"Unknown slide kind {kind!r}, skipping")
            sources_used.append(None)  # keep 1:1 alignment with content["slides"] / the kinds sidecar
            continue
        source = pick_source(kind)
        source_path, source_index = source
        if source_path == template_path:
            new_slide = _duplicate_slide(prs, source_index)
        else:
            new_slide = _copy_slide_from_external(prs, _get_template_prs(source_path), source_index)
        try:
            populate_slide_data(new_slide, kind, slide_data, source)
            sources_used.append(f"{source_path}::{source_index}")
        except Exception as e:
            logger.error(f"Failed to populate {kind!r} slide: {e}", exc_info=True)
            sources_used.append(None)

    _strip_original_slides(prs, original_count)
    prs.save(out_path)
    logger.info(f"Built {len(prs.slides)} slides from GD template → {out_path}")
    return sources_used


# ── Content generation — Ollama fills in a kind-aware slide schema ───────────

_TEMPLATE_PROMPT = """\
You are a professional presentation writer producing content for the Grid
Dynamics slide template. Return ONLY valid JSON — no markdown, no
explanation, nothing else.

Required format:
{{
  "presentation_title": "...",
  "slides": [ {{"kind": "...", ...fields for that kind...}}, ... ]
}}

Valid "kind" values and their fields (use whichever kinds best fit the
content — you do not need to use every kind, and you may repeat a kind):

- "agenda": {{"items": ["...", "..."]}} — 3-7 short agenda line items
- "text": {{"title": "...", "paragraphs": ["...", "..."]}} — 1-2 paragraphs of prose
- "two_column": {{"title": "...", "left": {{"heading":"...","body":"..."}}, "right": {{"heading":"...","body":"..."}}}}
- "comparison": same shape as "two_column" — use when contrasting two options
- "team": {{"title": "...", "people": [{{"name":"...","title":"..."}}, ...]}} — up to 6 people
- "speaker_1": {{"title": "...", "name": "...", "role": "...", "bio": "..."}}
- "speaker_4": {{"title": "...", "speakers": [{{"name":"...","role":"...","bio":"..."}}, ...]}} — exactly 4
- "text_blocks": {{"title": "...", "blocks": [{{"heading":"...","items":["...", "..."]}}, ...]}} — up to 4 blocks
- "key_message": {{"title": "...", "messages": ["...", "..."]}} — 1-2 short standalone statements
- "table": {{"title": "...", "headers": ["...", ...], "rows": [["...", ...], ...]}} — at most 5 columns
- "subsection": {{"title": "..."}} — a section-divider slide with just a title
- "chapter": {{"title": "...", "description": "..."}} — a section-divider slide with title + one-line description

Rules:
- Do NOT include "cover" or "thank_you" kinds — those are added automatically.
- "presentation_title" goes on the cover slide in a fixed-size box — keep it
  to 6 words / 40 characters or fewer, or it will overlap the date below it.
- Do NOT put "Grid Dynamics" in any title or heading — it is already shown
  elsewhere on every slide as the company name.
- Generate roughly {n} content slides total (not counting cover/thank_you).
- Prefer variety: don't use "text" for everything — pick the kind that best
  fits each piece of content (a table for numeric data, "comparison" for
  two contrasting options, "team"/"speaker_1"/"speaker_4" only if the topic
  actually involves people, etc).
- Every field is plain text — no markdown symbols (* # -) anywhere.
- Output MUST be complete valid JSON.

Topic: {topic}"""


def _repair_json(raw: str) -> str:
    """Best-effort repair for common LLM JSON quirks (markdown fences, // comments,
    trailing commas, missing commas, truncated output)."""
    raw = re.sub(r'```[a-z]*\n?', '', raw).replace('```', '')
    raw = re.sub(r'//[^\n]*', '', raw)
    raw = re.sub(r',\s*([\]}])', r'\1', raw)
    raw = re.sub(r'"\s*\n(\s*)"', '",\n\\1"', raw)
    raw = raw.strip()

    depth = []
    PAIRS = {'{': '}', '[': ']'}
    in_str = False
    escape = False
    for ch in raw:
        if escape:
            escape = False
            continue
        if ch == '\\' and in_str:
            escape = True
            continue
        if ch == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if ch in ('{', '['):
            depth.append(PAIRS[ch])
        elif ch in ('}', ']') and depth and depth[-1] == ch:
            depth.pop()

    raw = re.sub(r',?\s*"[^"]*$', '', raw)
    raw = re.sub(r',\s*$', '', raw)
    raw += ''.join(reversed(depth))
    return raw


_REQUIRED_FIELDS = {
    "agenda":      ["items"],
    "text":        ["title", "paragraphs"],
    "two_column":  ["title", "left", "right"],
    "comparison":  ["title", "left", "right"],
    "team":        ["title", "people"],
    "speaker_1":   ["title", "name", "bio"],
    "speaker_4":   ["title", "speakers"],
    "text_blocks": ["title", "blocks"],
    "key_message": ["title", "messages"],
    "table":       ["title", "headers", "rows"],
    "subsection":  ["title"],
    "chapter":     ["title", "description"],
}


def _generate_template_content_sync(description: str, slide_count: int) -> dict | None:
    try:
        import ollama
        from core.config import settings
        prompt = _TEMPLATE_PROMPT.format(n=slide_count, topic=description)

        response = ollama.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            format="json",
            options={"num_predict": -1, "num_ctx": 8192, "temperature": 0.6},
            stream=False,
        )
        raw = response.message.content if hasattr(response, "message") else response["message"]["content"]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            logger.warning(f"format=json still invalid ({e}), attempting repair")
            data = json.loads(_repair_json(raw))

        if "slides" not in data:
            logger.error(f"Response missing 'slides': {raw[:200]}")
            return None

        before = len(data["slides"])
        data["slides"] = [
            s for s in data["slides"]
            if s.get("kind") in _REQUIRED_FIELDS
            and all(s.get(f) for f in _REQUIRED_FIELDS[s["kind"]])
        ]
        dropped = before - len(data["slides"])
        if dropped:
            logger.warning(f"{dropped} slide(s) had an unknown kind or missing required fields and were removed")

        title = data.get("presentation_title") or "Generated Presentation"
        today = datetime.date.today().strftime("%B %Y")
        data["slides"] = (
            [{"kind": "cover", "title": title, "date": today}]
            + data["slides"]
            + [{"kind": "thank_you"}]
        )
        data["presentation_title"] = title

        logger.info(f"Generated {len(data['slides'])} slides for: {description[:50]}")
        return data

    except Exception as e:
        logger.error(f"Ollama template-content generate failed: {e}")
        return None


async def generate_template_content(description: str, slide_count: int) -> dict | None:
    return await asyncio.to_thread(_generate_template_content_sync, description, slide_count)


_SINGLE_SLIDE_PROMPT = """\
You are a professional presentation writer adding ONE new slide to an
existing Grid Dynamics presentation. Return ONLY valid JSON for that single
slide — no markdown, no explanation, nothing else, and no "slides" wrapper:
{{"kind": "...", ...fields for that kind...}}

Valid "kind" values and their fields (pick the ONE that best fits):
- "text": {{"title": "...", "paragraphs": ["...", "..."]}}
- "two_column": {{"title": "...", "left": {{"heading":"...","body":"..."}}, "right": {{"heading":"...","body":"..."}}}}
- "comparison": same shape as "two_column" — use when contrasting two options
- "team": {{"title": "...", "people": [{{"name":"...","title":"..."}}, ...]}} — up to 6 people
- "speaker_1": {{"title": "...", "name": "...", "role": "...", "bio": "..."}}
- "speaker_4": {{"title": "...", "speakers": [{{"name":"...","role":"...","bio":"..."}}, ...]}} — exactly 4
- "text_blocks": {{"title": "...", "blocks": [{{"heading":"...","items":["...", "..."]}}, ...]}} — up to 4 blocks
- "key_message": {{"title": "...", "messages": ["...", "..."]}} — 1-2 short standalone statements
- "table": {{"title": "...", "headers": ["...", ...], "rows": [["...", ...], ...]}} — at most 5 columns
- "subsection": {{"title": "..."}} — a section-divider slide with just a title
- "chapter": {{"title": "...", "description": "..."}} — a section-divider slide with title + one-line description
- "agenda": {{"items": ["...", "..."]}} — 3-7 short agenda line items

Rules:
- Do NOT put "Grid Dynamics" in any title or heading.
- Every field is plain text — no markdown symbols (* # -) anywhere.
- Output MUST be complete valid JSON, just the single slide object.

What the new slide should be about: {instruction}"""


def _generate_single_slide_sync(instruction: str) -> dict | None:
    try:
        import ollama
        from core.config import settings
        prompt = _SINGLE_SLIDE_PROMPT.format(instruction=instruction)

        response = ollama.chat(
            model=settings.OLLAMA_MODEL,
            messages=[{"role": "user", "content": prompt}],
            think=False,
            format="json",
            options={"num_predict": 500, "num_ctx": 4096, "temperature": 0.6},
            stream=False,
        )
        raw = response.message.content if hasattr(response, "message") else response["message"]["content"]

        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = json.loads(_repair_json(raw))

        kind = data.get("kind")
        if kind not in _REQUIRED_FIELDS or not all(data.get(f) for f in _REQUIRED_FIELDS[kind]):
            logger.warning(f"Single-slide generation produced an invalid slide: {raw[:200]}")
            return None

        return data
    except Exception as e:
        logger.error(f"Ollama single-slide generate failed: {e}")
        return None


async def generate_single_slide_content(instruction: str) -> dict | None:
    return await asyncio.to_thread(_generate_single_slide_sync, instruction)
