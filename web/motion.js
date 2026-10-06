const SVG_NS = "http://www.w3.org/2000/svg";
export const NORMAL_MOTION_MS = 5250;
export const REDUCED_MOTION_MS = 2200;
export const DRAW_MOTION_MS = 1550;

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
  // A tap is a real mark too; a move-only SVG path would disappear.
  if (stroke.length === 1) {
    const x = stroke[0].x * width, y = stroke[0].y * height;
    return `M${x.toFixed(2)} ${y.toFixed(2)} l0.01 0`;
  }
  return stroke.map((point, index) => `${index ? "L" : "M"}${(point.x * width).toFixed(2)} ${(point.y * height).toFixed(2)}`).join(" ");
}

export function handwrittenMotionBounds(handwritten) {
  const { width, height } = handwritten.coordinateSpace;
  // Presentation-only crop with breathing room for the recovered glow layers.
  // Original normalized coordinates and stroke order are never rewritten.
  const padding = 24;
  let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
  for (const stroke of handwritten.strokes) {
    for (const point of stroke) {
      const x = point.x * width, y = point.y * height;
      minX = Math.min(minX, x); minY = Math.min(minY, y);
      maxX = Math.max(maxX, x); maxY = Math.max(maxY, y);
    }
  }
  return { x: minX - padding, y: minY - padding, width: maxX - minX + padding * 2, height: maxY - minY + padding * 2 };
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

export function renderMotionMark(documentRef, target, mark, { kind = "add", imageAspectRatio = 1 } = {}) {
  target.replaceChildren();
  target.style.removeProperty("aspect-ratio");
  target.style.removeProperty("--handwritten-width");
  target.dataset.mode = mark.mode;
  target.dataset.kind = kind;
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
    // Measure intrinsic text at the enlarged font, then scale every layer
    // together. max-width alone does not fit long, unbroken names.
    const availableWidth = target.clientWidth;
    const imageHeight = target.parentElement?.clientHeight;
    let naturalWidth = typed.scrollWidth;
    let naturalHeight = typed.offsetHeight;
    if (availableWidth > 0 && naturalWidth > 0 && imageHeight > 0 && naturalHeight > 0) {
      if (availableWidth / (naturalWidth + 32) < .4) {
        // Extremely long names on narrow images remain readable: wrap the
        // complete identity rather than clipping/truncating it into tiny text.
        typed.style.maxWidth = `${Math.max(1, availableWidth / .4 - 32)}px`;
        typed.style.whiteSpace = "normal";
        typed.style.overflowWrap = "anywhere";
        typed.style.textAlign = "center";
        naturalWidth = typed.scrollWidth;
        naturalHeight = typed.offsetHeight;
      }
      const scale = Math.min(1, availableWidth / (naturalWidth + 32), imageHeight * .48 / (naturalHeight + 32));
      typed.style.setProperty("--typed-scale", String(scale));
    }
    return;
  }
  const { width, height } = mark.handwritten.coordinateSpace;
  const bounds = handwrittenMotionBounds(mark.handwritten);
  target.style.aspectRatio = `${bounds.width} / ${bounds.height}`;
  const imageRatio = Number.isFinite(imageAspectRatio) && imageAspectRatio > 0 ? imageAspectRatio : 1;
  // ~1.5x presence, with uniform height/paint bounds rather than distortion.
  // Existing absorption expands 4% and blurs; retain space around that glow.
  const widthPercent = Math.min(
    kind === "verify" ? 69 : 57,
    (kind === "verify" ? 69 : 63) * bounds.width / bounds.height / imageRatio,
    (kind === "verify" ? 92 : 88) * bounds.width / (bounds.width * 1.04 + 64),
    (kind === "verify" ? 92 : 88) * bounds.width / (bounds.height * 1.04 + 64) / imageRatio,
  );
  target.style.setProperty("--handwritten-width", `${widthPercent}%`);
  const svg = documentRef.createElementNS(SVG_NS, "svg");
  svg.classList.add("motion-handwritten");
  svg.setAttribute("viewBox", `${bounds.x} ${bounds.y} ${bounds.width} ${bounds.height}`);
  svg.setAttribute("preserveAspectRatio", "xMidYMid meet");
  svg.setAttribute("aria-hidden", "true");
  const lengths = mark.handwritten.strokes.map((stroke) => strokeLength(stroke, width, height));
  const total = lengths.reduce((sum, value) => sum + value, 0);
  const floor = Math.min(120, DRAW_MOTION_MS / Math.max(1, lengths.length));
  const budget = Math.max(0, DRAW_MOTION_MS - floor * lengths.length);
  let elapsed = 0;
  let scheduled = 0;
  mark.handwritten.strokes.forEach((stroke, index) => {
    scheduled += floor + budget * lengths[index] / total;
    const duration = (index === lengths.length - 1 ? DRAW_MOTION_MS : Math.round(scheduled)) - elapsed;
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
