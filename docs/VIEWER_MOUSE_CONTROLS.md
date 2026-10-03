# Viewer mouse controls — beta.6

## Time-axis navigation

- Mouse wheel: X-axis zoom around the mouse position.
- Shift + mouse wheel: pan the time axis.
- Right mouse drag: continuously pan the shared time axis. The pointer changes while dragging.
- Range zoom button / Z: existing span-zoom mode remains available.

## Measurement cursors A-Z

- Move the pointer near a visible vertical measurement cursor.
- Within approximately 8 display pixels, the native pointer changes to a horizontal-resize cursor.
- Left-drag the line to move that exact cursor.
- Clicking away from an existing cursor retains the existing cursor-placement behavior.
- A-Z keys select a cursor; Left/Right moves by one sample; Shift+Left/Right moves by ten samples.

Cursor hit testing is performed in display pixels, so the grab width stays usable at different X-axis zoom levels.
