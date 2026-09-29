import { deepFreeze, fail } from "./errors.js";
import { ASPECT_MAX, ASPECT_MIN, COORDINATE_MAX, COORDINATE_MIN } from "./contract.js";

function finite(value, field) {
  if (typeof value !== "number") fail("INVALID_FIELD_TYPE", { field, expected: "number" });
  if (!Number.isFinite(value)) fail("INVALID_NUMBER", { field });
  return value;
}

export function createCapturePlane(width, height) {
  finite(width, "width");
  finite(height, "height");
  if (!Number.isInteger(width) || !Number.isInteger(height)) fail("INVALID_FIELD_TYPE", { field: "coordinateSpace", expected: "integer_dimensions" });
  if (width < COORDINATE_MIN || width > COORDINATE_MAX || height < COORDINATE_MIN || height > COORDINATE_MAX) {
    fail("OUT_OF_RANGE", { field: "coordinateSpace" });
  }
  const aspect = width / height;
  if (aspect < ASPECT_MIN || aspect > ASPECT_MAX) fail("OUT_OF_RANGE", { field: "coordinateSpace.aspect" });
  return deepFreeze({ width, height });
}

export function centerFitTransform(coordinateSpace, container) {
  const plane = createCapturePlane(coordinateSpace?.width, coordinateSpace?.height);
  const x = finite(container?.x, "container.x");
  const y = finite(container?.y, "container.y");
  const width = finite(container?.width, "container.width");
  const height = finite(container?.height, "container.height");
  if (width <= 0 || height <= 0) fail("OUT_OF_RANGE", { field: "container", reason: "positive_dimensions_required" });
  const scale = Math.min(width / plane.width, height / plane.height);
  const fittedWidth = scale * plane.width;
  const fittedHeight = scale * plane.height;
  return deepFreeze({
    coordinateSpace: plane,
    container: { x, y, width, height },
    scale,
    x: x + (width - fittedWidth) / 2,
    y: y + (height - fittedHeight) / 2,
    width: fittedWidth,
    height: fittedHeight,
  });
}

export function mapNormalizedPoint(transform, point) {
  const x = finite(point?.x, "point.x");
  const y = finite(point?.y, "point.y");
  if (x < 0 || x > 1 || y < 0 || y > 1) fail("OUT_OF_RANGE", { field: "point" });
  return deepFreeze({ x: transform.x + x * transform.width, y: transform.y + y * transform.height });
}

export function inverseMapPoint(transform, pointer, { clampActiveStroke = false } = {}) {
  const px = finite(pointer?.x, "pointer.x");
  const py = finite(pointer?.y, "pointer.y");
  const rawX = (px - transform.x) / transform.width;
  const rawY = (py - transform.y) / transform.height;
  const inside = rawX >= 0 && rawX <= 1 && rawY >= 0 && rawY <= 1;
  if (!inside && !clampActiveStroke) return deepFreeze({ state: "OUTSIDE" });
  return deepFreeze({
    state: inside ? "INSIDE" : "CLAMPED",
    point: { x: Math.min(1, Math.max(0, rawX)), y: Math.min(1, Math.max(0, rawY)) },
  });
}
