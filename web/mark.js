export const DRAW_WIDTH = 480;
export const DRAW_HEIGHT = 220;

export function blankPersonalMarkDraft() {
  return {
    version: 1,
    mode: "typed",
    typed: "",
    handwritten: {
      coordinateSpace: { width: DRAW_WIDTH, height: DRAW_HEIGHT },
      strokes: [],
    },
  };
}

export function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function validPoint(point) {
  return point && Number.isFinite(point.x) && Number.isFinite(point.y)
    && point.x >= 0 && point.x <= 1 && point.y >= 0 && point.y <= 1;
}

export function sanitizeMark(value) {
  if (!value || value.version !== 1 || !["typed", "handwritten"].includes(value.mode)) return null;
  const typed = typeof value.typed === "string" ? value.typed.trim().slice(0, 48) : "";
  const space = value.handwritten?.coordinateSpace;
  const sourceStrokes = value.handwritten?.strokes;
  if (!space || !Number.isFinite(space.width) || !Number.isFinite(space.height)
    || space.width <= 0 || space.height <= 0 || !Array.isArray(sourceStrokes)) return null;
  const strokes = sourceStrokes.filter(Array.isArray).slice(0, 128)
    .map((stroke) => stroke.filter(validPoint).slice(0, 4096))
    .filter((stroke) => stroke.length > 0);
  if ((value.mode === "typed" && !typed) || (value.mode === "handwritten" && !strokes.length)) return null;
  return {
    version: 1,
    mode: value.mode,
    typed,
    handwritten: {
      // F2A.1 view-model metadata only. This is not a production v2 schema.
      coordinateSpace: { width: space.width, height: space.height },
      strokes,
    },
  };
}
