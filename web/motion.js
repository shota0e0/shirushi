const SVG_NS = "http://www.w3.org/2000/svg";
export const NORMAL_MOTION_MS = 5250;
export const REDUCED_MOTION_MS = 2200;

export function shouldReduceMotion(queryOverride, systemPrefersReduced) {
  if (queryOverride === "reduce") return true;
  if (queryOverride === "normal") return false;
  return systemPrefersReduced === true;
}

export function motionCopyKeys(kind) {
  if (kind === "verify") return { complete: "motionVerify", progress: "statusVerifyPreview", done: "statusVerifyPreviewDone" };
  return { complete: "motionAdd", progress: "statusAddingPreview", done: "statusAddPreviewDone" };
}

export function resetMotionClasses(classList) {
  classList.remove("playing", "reduced", "motion-add", "motion-verify");
}

function pathData(stroke, width, height) {
  return stroke.map((point, index) => `${index ? "L" : "M"}${(point.x * width).toFixed(2)} ${(point.y * height).toFixed(2)}`).join(" ");
}

function strokeLength(stroke, width, height) {
  let length = 0;
  for (let index = 1; index < stroke.length; index += 1) {
    length += Math.hypot((stroke[index].x - stroke[index - 1].x) * width, (stroke[index].y - stroke[index - 1].y) * height);
  }
  return Math.max(length, 1);
}

export function createStrokeSvg(documentRef, handwritten, className) {
  const { width, height } = handwritten.coordinateSpace;
  const svg = documentRef.createElementNS(SVG_NS, "svg");
  svg.classList.add(className);
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("aria-hidden", "true");
  handwritten.strokes.forEach((stroke) => {
    const path = documentRef.createElementNS(SVG_NS, "path");
    path.setAttribute("d", pathData(stroke, width, height));
    svg.append(path);
  });
  return svg;
}

export function renderMotionMark(documentRef, target, mark) {
  target.replaceChildren();
  target.style.removeProperty("aspect-ratio");
  target.dataset.mode = mark.mode;
  if (mark.mode === "typed") {
    const typed = documentRef.createElement("div");
    typed.className = "motion-typed";
    for (const className of ["motion-typed-aura", "motion-typed-text", "motion-typed-trace", "motion-typed-fragments"]) {
      const span = documentRef.createElement("span");
      span.className = className;
      if (className.endsWith("aura") || className.endsWith("text")) span.textContent = mark.typed;
      if (!className.endsWith("text")) span.setAttribute("aria-hidden", "true");
      typed.append(span);
    }
    target.append(typed);
    return;
  }
  const { width, height } = mark.handwritten.coordinateSpace;
  target.style.aspectRatio = `${width} / ${height}`;
  const svg = documentRef.createElementNS(SVG_NS, "svg");
  svg.classList.add("motion-handwritten");
  svg.setAttribute("viewBox", `0 0 ${width} ${height}`);
  svg.setAttribute("aria-hidden", "true");
  const lengths = mark.handwritten.strokes.map((stroke) => strokeLength(stroke, width, height));
  const total = lengths.reduce((sum, value) => sum + value, 0);
  const floor = Math.min(120, 1550 / Math.max(1, lengths.length));
  const budget = Math.max(0, 1550 - floor * lengths.length);
  let elapsed = 0;
  mark.handwritten.strokes.forEach((stroke, index) => {
    const duration = Math.round(floor + budget * lengths[index] / total);
    for (const className of ["diffusion", "glow", "ink"]) {
      const path = documentRef.createElementNS(SVG_NS, "path");
      path.setAttribute("d", pathData(stroke, width, height));
      path.setAttribute("pathLength", "1");
      path.classList.add(className);
      path.style.setProperty("--stroke-delay", `${elapsed}ms`);
      path.style.setProperty("--stroke-duration", `${duration}ms`);
      svg.append(path);
    }
    elapsed += duration;
  });
  target.append(svg);
}
