"""
Fullscreen region-selection overlay.

Displays a tone-mapped preview of the captured HDR frame on the correct
monitor so the user can drag a crop region.  Returns coordinates in the
monitor's local space (origin = monitor top-left corner), which matches
the numpy array returned by capture.grab().
"""
import tkinter as tk
from PIL import Image, ImageTk

from capture import MonitorInfo


def select_region(
    preview: Image.Image,
    monitor: MonitorInfo,
) -> "tuple[int,int,int,int] | None":
    """
    Show a fullscreen overlay on *monitor* with *preview* as background.

    Args:
        preview:  SDR PIL Image (full monitor frame, already tone-mapped).
        monitor:  MonitorInfo for the target monitor.

    Returns:
        (x1, y1, x2, y2) in monitor-local coordinates, or None if cancelled.
    """
    result: list[tuple | None] = [None]

    root = tk.Tk()
    root.overrideredirect(True)
    root.attributes("-topmost", True)
    # Position exactly over the target monitor (handles negative offsets too)
    root.geometry(f"{monitor.width}x{monitor.height}+{monitor.left}+{monitor.top}")

    # Scale preview to the monitor size
    scaled  = preview.resize((monitor.width, monitor.height), Image.LANCZOS)
    photo   = ImageTk.PhotoImage(scaled)

    canvas = tk.Canvas(root, cursor="crosshair", highlightthickness=0, bd=0)
    canvas.pack(fill="both", expand=True)
    canvas.create_image(0, 0, anchor="nw", image=photo)

    # Light dim overlay so the content stays readable during selection
    # gray50 = 50 % pixels visible (gray25 was too dark)
    canvas.create_rectangle(
        0, 0, monitor.width, monitor.height,
        fill="black", stipple="gray50", outline="",
    )

    # Instruction label
    canvas.create_text(
        monitor.width // 2, 30,
        text="Drag to select region  •  Esc to cancel",
        fill="white", font=("Segoe UI", 14),
    )

    start_x = start_y = 0
    rect_id:  list[int | None] = [None]
    label_id: list[int | None] = [None]

    def _on_press(e: tk.Event) -> None:
        nonlocal start_x, start_y
        start_x, start_y = e.x, e.y
        for i in (rect_id, label_id):
            if i[0]:
                canvas.delete(i[0])

    def _on_drag(e: tk.Event) -> None:
        for i in (rect_id, label_id):
            if i[0]:
                canvas.delete(i[0])

        x1, y1 = min(start_x, e.x), min(start_y, e.y)
        x2, y2 = max(start_x, e.x), max(start_y, e.y)

        rect_id[0] = canvas.create_rectangle(
            x1, y1, x2, y2,
            outline="#ff3333", width=2, dash=(6, 3),
        )
        label_id[0] = canvas.create_text(
            x1 + 4, y1 - 12 if y1 > 20 else y2 + 12,
            text=f"{x2-x1} × {y2-y1}",
            fill="#ff3333", font=("Segoe UI", 11), anchor="w",
        )

    def _on_release(e: tk.Event) -> None:
        x1 = min(start_x, e.x)
        y1 = min(start_y, e.y)
        x2 = max(start_x, e.x)
        y2 = max(start_y, e.y)
        if x2 - x1 >= 4 and y2 - y1 >= 4:
            result[0] = (x1, y1, x2, y2)
        root.destroy()

    canvas.bind("<ButtonPress-1>",   _on_press)
    canvas.bind("<B1-Motion>",       _on_drag)
    canvas.bind("<ButtonRelease-1>", _on_release)
    root.bind("<Escape>",            lambda e: root.destroy())

    root.mainloop()
    return result[0]
