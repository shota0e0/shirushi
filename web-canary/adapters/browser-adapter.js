const STORAGE_KEY = "shirushi.canary.personal-mark.v1";

function defaultHandwrittenMark() {
  return {
    coordinateSpace: { width: 480, height: 220 },
    strokes: [
      [
        { x: 0.13, y: 0.69 },
        { x: 0.19, y: 0.53 },
        { x: 0.27, y: 0.35 },
        { x: 0.32, y: 0.25 },
        { x: 0.34, y: 0.37 },
        { x: 0.31, y: 0.59 },
        { x: 0.29, y: 0.73 },
      ],
      [
        { x: 0.23, y: 0.55 },
        { x: 0.34, y: 0.49 },
        { x: 0.46, y: 0.47 },
        { x: 0.56, y: 0.5 },
        { x: 0.62, y: 0.59 },
        { x: 0.6, y: 0.68 },
        { x: 0.51, y: 0.72 },
        { x: 0.42, y: 0.68 },
      ],
      [
        { x: 0.48, y: 0.3 },
        { x: 0.56, y: 0.41 },
        { x: 0.66, y: 0.51 },
        { x: 0.75, y: 0.57 },
        { x: 0.83, y: 0.55 },
      ],
    ],
  };
}

function storedVerificationSnapshot(mode = "typed") {
  const handwritten = {
    coordinateSpace: { width: 520, height: 220 },
    strokes: [
      [
        { x: 0.18, y: 0.76 },
        { x: 0.19, y: 0.59 },
        { x: 0.2, y: 0.39 },
        { x: 0.21, y: 0.22 },
        { x: 0.34, y: 0.2 },
        { x: 0.46, y: 0.25 },
        { x: 0.51, y: 0.35 },
        { x: 0.45, y: 0.45 },
        { x: 0.32, y: 0.49 },
        { x: 0.21, y: 0.48 },
      ],
      [
        { x: 0.34, y: 0.48 },
        { x: 0.43, y: 0.57 },
        { x: 0.52, y: 0.69 },
        { x: 0.59, y: 0.78 },
      ],
      [
        { x: 0.48, y: 0.66 },
        { x: 0.59, y: 0.57 },
        { x: 0.69, y: 0.61 },
        { x: 0.79, y: 0.55 },
        { x: 0.85, y: 0.43 },
      ],
    ],
  };
  return {
    source: "canary-file-snapshot",
    snapshotMode: mode,
    rightsIntent: "no_ai_training_or_generation",
    mark: {
      version: 1,
      mode,
      typed: "Mori",
      handwritten,
    },
  };
}

export function defaultPersonalMark() {
  return {
    version: 1,
    mode: "typed",
    typed: "Niki",
    handwritten: defaultHandwrittenMark(),
  };
}

function validatePoint(point) {
  return (
    point &&
    Number.isFinite(point.x) &&
    Number.isFinite(point.y) &&
    point.x >= 0 &&
    point.x <= 1 &&
    point.y >= 0 &&
    point.y <= 1
  );
}

function sanitizeMark(value) {
  if (!value || value.version !== 1 || !["typed", "handwritten"].includes(value.mode)) return null;
  const typed = typeof value.typed === "string" ? value.typed.trim().slice(0, 48) : "";
  const source = value.handwritten?.coordinateSpace;
  const strokes = value.handwritten?.strokes;
  if (
    !source ||
    !Number.isFinite(source.width) ||
    !Number.isFinite(source.height) ||
    source.width <= 0 ||
    source.height <= 0 ||
    !Array.isArray(strokes)
  ) {
    return null;
  }
  const cleanStrokes = strokes
    .filter(Array.isArray)
    .slice(0, 128)
    .map((stroke) => stroke.filter(validatePoint).slice(0, 4096))
    .filter((stroke) => stroke.length > 0);
  if ((value.mode === "typed" && !typed) || (value.mode === "handwritten" && !cleanStrokes.length)) {
    return null;
  }
  return {
    version: 1,
    mode: value.mode,
    typed,
    handwritten: {
      coordinateSpace: { width: source.width, height: source.height },
      strokes: cleanStrokes,
    },
  };
}

export class BrowserAdapter {
  #objectUrl = null;

  loadPersonalMark() {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY));
      return sanitizeMark(saved) || defaultPersonalMark();
    } catch {
      return defaultPersonalMark();
    }
  }

  savePersonalMark(mark) {
    const safe = sanitizeMark(mark);
    if (!safe) throw new Error("INVALID_PERSONAL_MARK");
    localStorage.setItem(STORAGE_KEY, JSON.stringify(safe));
    return safe;
  }

  imageFromFile(file) {
    if (!(file instanceof File) || !file.type.startsWith("image/")) {
      throw new Error("INVALID_IMAGE");
    }
    this.releaseImage();
    this.#objectUrl = URL.createObjectURL(file);
    return { url: this.#objectUrl, name: file.name, demo: false };
  }

  demoImage() {
    this.releaseImage();
    return { url: "./assets/demo-art.svg", nameKey: "demoImageName", demo: true };
  }

  releaseImage() {
    if (this.#objectUrl) {
      URL.revokeObjectURL(this.#objectUrl);
      this.#objectUrl = null;
    }
  }

  async addMark() {
    // The F1 Browser Adapter deliberately simulates only the SUCCESS contract.
    // It never rewrites the chosen image and is not a Python Core substitute.
    await new Promise((resolve) => window.setTimeout(resolve, 140));
    return { status: "SUCCESS", source: "canary-add-simulation", canary: true };
  }

  async inspectStoredSnapshot(mode = "typed") {
    // F1 Adjustment 02 fixtures: these are file-owned Typed/Handwritten
    // snapshot stand-ins, never the current browser profile mark and never
    // real C2PA readback.
    if (!["typed", "handwritten"].includes(mode)) throw new Error("INVALID_CANARY_SNAPSHOT_MODE");
    await new Promise((resolve) => window.setTimeout(resolve, 140));
    const snapshot = storedVerificationSnapshot(mode);
    return {
      status: "VERIFIED",
      source: snapshot.source,
      snapshotMode: snapshot.snapshotMode,
      rightsIntent: snapshot.rightsIntent,
      mark: sanitizeMark(snapshot.mark),
      canary: true,
    };
  }
}
