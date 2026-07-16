"""
uno_bridge.py
=============
LibreOfficeDocumentService — a clean UNO wrapper for slide editing.

IMPORTANT: This module MUST be run under the LibreOffice-bundled Python 3.12
runtime, or a Python 3.12 interpreter with pyuno.so on PYTHONPATH.
Never import this inside the main FastAPI (Python 3.11) process.

Typical use:
    service = LibreOfficeDocumentService(port=2002)
    service.connect()
    service.open_document("/path/to/file.pptx")
    service.add_slide(index=0, layout_index=0)
    png_bytes = service.get_slide_thumbnail(index=0)
    service.save()
    service.close()
"""

from __future__ import annotations
import io
import os
import tempfile
import threading
import time

# UNO imports — only available inside pyuno/LibreOffice Python
import uno
from com.sun.star.beans import PropertyValue
from com.sun.star.lang import XComponent
from com.sun.star.drawing import XDrawPagesSupplier
from com.sun.star.presentation import XPresentationPage

UNO_LOCK = threading.Lock()


def _make_property(name: str, value) -> PropertyValue:
    prop = PropertyValue()
    prop.Name = name
    prop.Value = value
    return prop


class LibreOfficeDocumentService:
    """
    Thread-safe UNO document service for a single open presentation.
    All public methods acquire UNO_LOCK before making UNO calls.
    """

    def __init__(self, port: int = 2002, timeout: float = 10.0):
        self.port = port
        self.timeout = timeout
        self._ctx = None
        self._smgr = None
        self._desktop = None
        self._doc = None  # com.sun.star.lang.XComponent

    # ------------------------------------------------------------------
    # Connection & Lifecycle
    # ------------------------------------------------------------------

    def connect(self):
        """Connect to the running headless soffice UNO listener."""
        localCtx = uno.getComponentContext()
        resolver = localCtx.ServiceManager.createInstanceWithContext(
            "com.sun.star.bridge.UnoUrlResolver", localCtx
        )

        deadline = time.time() + self.timeout
        last_err = None
        while time.time() < deadline:
            try:
                self._ctx = resolver.resolve(
                    f"uno:socket,host=localhost,port={self.port};"
                    "urp;StarOffice.ComponentContext"
                )
                break
            except Exception as e:
                last_err = e
                time.sleep(0.5)

        if self._ctx is None:
            raise ConnectionError(
                f"Cannot connect to LibreOffice UNO socket on port {self.port}: {last_err}"
            )

        self._smgr = self._ctx.ServiceManager
        self._desktop = self._smgr.createInstanceWithContext(
            "com.sun.star.frame.Desktop", self._ctx
        )

    def is_connected(self) -> bool:
        try:
            if self._desktop is None:
                return False
            _ = self._desktop.supportsService("com.sun.star.frame.Desktop")
            return True
        except Exception:
            return False

    def create_blank_presentation(self):
        """Open a fresh blank Impress presentation."""
        with UNO_LOCK:
            props = [
                _make_property("Hidden", True),
                _make_property("MacroExecutionMode", 4),
            ]
            self._doc = self._desktop.loadComponentFromURL(
                "private:factory/simpress", "_blank", 0, props
            )

    def open_document(self, path: str):
        """Open an existing .pptx/.ppt/.odp file."""
        with UNO_LOCK:
            url = uno.systemPathToFileUrl(os.path.abspath(path))
            props = [
                _make_property("Hidden", True),
                _make_property("ReadOnly", False),
                _make_property("MacroExecutionMode", 4),
            ]
            self._doc = self._desktop.loadComponentFromURL(url, "_blank", 0, props)

    def save(self, path: str | None = None):
        """Save in-place, or store to a new path."""
        with UNO_LOCK:
            if path:
                url = uno.systemPathToFileUrl(os.path.abspath(path))
                filter_name = "Impress MS PowerPoint 2007 XML" if path.endswith(".pptx") else "impress8"
                props = [
                    _make_property("FilterName", filter_name),
                    _make_property("Overwrite", True),
                ]
                self._doc.storeToURL(url, props)
            else:
                self._doc.store()

    def close(self):
        """Close the currently open document."""
        with UNO_LOCK:
            if self._doc:
                try:
                    self._doc.close(True)
                except Exception:
                    pass
                self._doc = None

    # ------------------------------------------------------------------
    # Slide Management
    # ------------------------------------------------------------------

    def _draw_pages(self):
        supplier = self._doc.queryInterface(XDrawPagesSupplier)
        return supplier.DrawPages

    def get_slide_count(self) -> int:
        with UNO_LOCK:
            return self._draw_pages().Count

    def add_slide(self, index: int | None = None, layout_index: int = 1) -> int:
        """
        Append a new slide (or insert at index).
        layout_index: 0=blank, 1=title+content, 20=title only, etc.
        Returns the actual index of the new slide.
        """
        with UNO_LOCK:
            pages = self._draw_pages()
            count = pages.Count
            insert_at = count if index is None else min(max(index, 0), count)
            pages.insertNewByIndex(insert_at)
            new_slide = pages.getByIndex(insert_at)
            new_slide.Layout = layout_index
            return insert_at

    def delete_slide(self, index: int):
        """Delete the slide at zero-based index."""
        with UNO_LOCK:
            pages = self._draw_pages()
            if index < 0 or index >= pages.Count:
                raise IndexError(f"Slide index {index} out of range (0-{pages.Count - 1})")
            slide = pages.getByIndex(index)
            pages.remove(slide)

    def duplicate_slide(self, index: int) -> int:
        """Duplicate the slide at index, insert after it, return new index."""
        with UNO_LOCK:
            pages = self._draw_pages()
            src = pages.getByIndex(index)
            new_index = index + 1
            pages.insertNewByIndex(new_index)
            dst = pages.getByIndex(new_index)
            # Copy shape content across via serialization
            src.copyShapes(dst)
            dst.Layout = src.Layout
            return new_index

    def reorder_slides(self, new_order: list[int]):
        """
        Reorder slides given a permutation list of current indices.
        new_order = [2, 0, 1] means: put slide-2 first, slide-0 second, slide-1 third.
        """
        with UNO_LOCK:
            pages = self._draw_pages()
            count = pages.Count
            if sorted(new_order) != list(range(count)):
                raise ValueError("new_order must be a complete permutation of [0..n-1]")
            # Move one at a time using moveByIndex (Impress API)
            for target_pos, src_idx in enumerate(new_order):
                # Find current position of the slide originally at src_idx
                # After each move positions shift; track using Name
                slide_to_move = pages.getByIndex(src_idx)
                pages.moveByIndex(src_idx, target_pos)

    # ------------------------------------------------------------------
    # Content & Formatting
    # ------------------------------------------------------------------

    def set_slide_title(self, slide_index: int, title: str):
        """Set the title text of a slide's title shape (index 0 or 1)."""
        with UNO_LOCK:
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)
            # Impress layouts: shape[0] is usually title, shape[1] content
            for i in range(slide.Count):
                shape = slide.getByIndex(i)
                try:
                    if shape.supportsService("com.sun.star.presentation.TitleTextShape"):
                        shape.setString(title)
                        return
                except Exception:
                    pass

    def set_slide_content(self, slide_index: int, lines: list[str]):
        """Replace the content (body/bullet) shape text with given lines."""
        with UNO_LOCK:
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)
            for i in range(slide.Count):
                shape = slide.getByIndex(i)
                try:
                    if shape.supportsService("com.sun.star.presentation.SubtitleTextShape") or \
                       shape.supportsService("com.sun.star.drawing.plugin.presentation.SubtitleTextShape") or \
                       shape.supportsService("com.sun.star.presentation.BodyText"):
                        tf = shape.getText()
                        cursor = tf.createTextCursor()
                        cursor.gotoStart(False)
                        cursor.gotoEnd(True)
                        tf.insertString(cursor, "\n".join(lines), True)
                        return
                except Exception:
                    pass

    def edit_text_in_shape(self, slide_index: int, shape_id: int, new_text: str):
        """
        Directly edit text of a shape by zero-based shape_id on a slide.
        If shape has multiple paragraphs, the whole text is replaced.
        """
        with UNO_LOCK:
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)
            shape = slide.getByIndex(shape_id)
            if not shape.supportsService("com.sun.star.drawing.Text"):
                raise TypeError(f"Shape {shape_id} on slide {slide_index} has no text.")
            tf = shape.getText()
            cursor = tf.createTextCursor()
            cursor.gotoStart(False)
            cursor.gotoEnd(True)
            tf.insertString(cursor, new_text, True)

    def apply_font_to_shape(
        self,
        slide_index: int,
        shape_id: int,
        font_name: str | None = None,
        font_size_pt: float | None = None,
        bold: bool | None = None,
        italic: bool | None = None,
        color_hex: str | None = None,
    ):
        """Apply font properties to all text in a shape."""
        with UNO_LOCK:
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)
            shape = slide.getByIndex(shape_id)
            tf = shape.getText()
            cursor = tf.createTextCursor()
            cursor.gotoStart(False)
            cursor.gotoEnd(True)
            if font_name is not None:
                cursor.setPropertyValue("CharFontName", font_name)
            if font_size_pt is not None:
                cursor.setPropertyValue("CharHeight", float(font_size_pt))
            if bold is not None:
                from com.sun.star.awt import FontWeight
                cursor.setPropertyValue("CharWeight", FontWeight.BOLD if bold else FontWeight.NORMAL)
            if italic is not None:
                from com.sun.star.awt import FontSlant
                cursor.setPropertyValue("CharPosture", FontSlant.ITALIC if italic else FontSlant.NONE)
            if color_hex is not None:
                # color_hex like "#F5A700" or "F5A700"
                hex_val = color_hex.lstrip("#")
                color_int = int(hex_val, 16)
                cursor.setPropertyValue("CharColor", color_int)

    def apply_background_color(self, slide_index: int, color_hex: str):
        """Apply a solid background fill color to the slide."""
        with UNO_LOCK:
            from com.sun.star.drawing import FillStyle
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)
            hex_val = color_hex.lstrip("#")
            color_int = int(hex_val, 16)
            background = self._doc.createInstance("com.sun.star.drawing.Background")
            background.FillStyle = FillStyle.SOLID
            background.FillColor = color_int
            slide.Background = background

    # ------------------------------------------------------------------
    # Image Insertion
    # ------------------------------------------------------------------

    def add_image(
        self,
        slide_index: int,
        image_path: str,
        x_cm: float = 2.0,
        y_cm: float = 5.0,
        width_cm: float = 10.0,
        height_cm: float = 7.0,
    ) -> int:
        """
        Insert an image onto a slide.
        Returns the shape index of the newly inserted image shape.
        """
        with UNO_LOCK:
            from com.sun.star.awt import Size, Point
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)

            image_url = uno.systemPathToFileUrl(os.path.abspath(image_path))

            image_shape = self._doc.createInstance("com.sun.star.drawing.GraphicObjectShape")
            slide.add(image_shape)

            image_shape.GraphicURL = image_url  # Deprecated in LO7+, still works
            try:
                # New LO 7+ API
                from com.sun.star.graphic import XGraphicProvider
                gp = self._smgr.createInstanceWithContext(
                    "com.sun.star.graphic.GraphicProvider", self._ctx
                )
                props = [_make_property("URL", image_url)]
                graphic = gp.queryGraphic(props)
                image_shape.Graphic = graphic
            except Exception:
                pass

            # Convert cm to 1/100 mm (UNO unit)
            image_shape.Size = Size(int(width_cm * 1000), int(height_cm * 1000))
            image_shape.Position = Point(int(x_cm * 1000), int(y_cm * 1000))

            return slide.Count - 1

    # ------------------------------------------------------------------
    # Thumbnail Export
    # ------------------------------------------------------------------

    def get_slide_thumbnail(self, index: int, width_px: int = 1280, height_px: int = 720) -> bytes:
        """
        Export a single slide as PNG bytes at the specified pixel size.
        Uses UNO's GraphicExportFilter for true LibreOffice rendering.
        """
        with UNO_LOCK:
            pages = self._draw_pages()
            slide = pages.getByIndex(index)

            exporter = self._smgr.createInstanceWithContext(
                "com.sun.star.drawing.GraphicExportFilter", self._ctx
            )
            exporter.setSourceDocument(slide)

            with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
                tmp_path = f.name

            try:
                export_url = uno.systemPathToFileUrl(tmp_path)
                props = [
                    _make_property("MediaType", "image/png"),
                    _make_property("URL", export_url),
                    _make_property("Width", width_px),
                    _make_property("Height", height_px),
                ]
                exporter.filter(props)

                with open(tmp_path, "rb") as f:
                    return f.read()
            finally:
                try:
                    os.unlink(tmp_path)
                except OSError:
                    pass

    def get_all_thumbnails(self, width_px: int = 1280, height_px: int = 720) -> list[bytes]:
        """Export all slide thumbnails, returns list of PNG bytes."""
        count = self.get_slide_count()
        return [self.get_slide_thumbnail(i, width_px, height_px) for i in range(count)]

    # ------------------------------------------------------------------
    # Master Slides & Layouts
    # ------------------------------------------------------------------

    def list_available_layouts(self) -> list[str]:
        """Returns a list of master page names available in the document."""
        with UNO_LOCK:
            master_pages = self._doc.MasterPages
            return [master_pages.getByIndex(i).Name for i in range(master_pages.Count)]

    def apply_master_slide(self, slide_index: int, master_name: str):
        """Apply a master slide by name to a specific slide."""
        with UNO_LOCK:
            pages = self._draw_pages()
            slide = pages.getByIndex(slide_index)
            master_pages = self._doc.MasterPages
            for i in range(master_pages.Count):
                mp = master_pages.getByIndex(i)
                if mp.Name == master_name:
                    slide.MasterPage = mp
                    return
            raise ValueError(f"Master slide '{master_name}' not found. Available: {self.list_available_layouts()}")

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------

    def export(self, output_path: str, fmt: str = "pptx"):
        """
        Export the open document to a file.
        fmt: 'pptx' | 'pdf' | 'odp'
        """
        fmt_map = {
            "pptx": "Impress MS PowerPoint 2007 XML",
            "pdf":  "impress_pdf_Export",
            "odp":  "impress8",
        }
        filter_name = fmt_map.get(fmt.lower())
        if not filter_name:
            raise ValueError(f"Unsupported export format '{fmt}'. Use: pptx, pdf, odp")
        with UNO_LOCK:
            url = uno.systemPathToFileUrl(os.path.abspath(output_path))
            props = [
                _make_property("FilterName", filter_name),
                _make_property("Overwrite", True),
            ]
            self._doc.storeToURL(url, props)

    # ------------------------------------------------------------------
    # Macro Execution
    # ------------------------------------------------------------------

    def run_macro(self, macro_name: str, args: list | None = None) -> any:
        """
        Run a Basic macro by fully-qualified name like:
            "Standard.Module1.MacroName"
        Returns the macro's return value.
        """
        with UNO_LOCK:
            macro_args = uno.Any("[]com.sun.star.beans.PropertyValue", ())
            script_ctx = self._smgr.createInstanceWithContext(
                "com.sun.star.script.provider.MasterScriptProviderFactory", self._ctx
            )
            # Build a script URL in the Basic URI scheme
            script_url = f"macro:///{ macro_name }"
            provider = self._doc.getScriptProvider()
            script = provider.getScript(script_url)
            out_args = []
            result = script.invoke((args or [],), out_args, [])
            return result

    # ------------------------------------------------------------------
    # ModifyListener support
    # ------------------------------------------------------------------

    def add_modify_listener(self, callback):
        """
        Register a callable as a document modify listener.
        callback(event) is called on every document change.

        NOTE: In the UNO worker process this is used to push events
        into a multiprocessing Queue that the FastAPI bridge polls.
        """
        from com.sun.star.util import XModifyListener

        class _Listener(unohelper.Base, XModifyListener):
            def __init__(self, cb):
                self._cb = cb

            def modified(self, event):
                try:
                    self._cb(event)
                except Exception:
                    pass

            def disposing(self, event):
                pass

        import unohelper  # noqa — available inside pyuno context only
        listener = _Listener(callback)
        self._doc.addModifyListener(listener)
        return listener  # keep reference to avoid GC
