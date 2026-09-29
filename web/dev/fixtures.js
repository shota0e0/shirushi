export const PREVIEW_PROVENANCE = "f1-approved-isolated-dev-fixture";

export function previewProfileFixture() {
  return {
    version: 1,
    mode: "typed",
    typed: "Niki",
    handwritten: {
      coordinateSpace: { width: 480, height: 220 },
      strokes: [
        [{ x: 0.13, y: 0.69 }, { x: 0.19, y: 0.53 }, { x: 0.27, y: 0.35 }, { x: 0.34, y: 0.25 }, { x: 0.31, y: 0.59 }, { x: 0.29, y: 0.73 }],
        [{ x: 0.23, y: 0.55 }, { x: 0.34, y: 0.49 }, { x: 0.46, y: 0.47 }, { x: 0.56, y: 0.5 }, { x: 0.62, y: 0.59 }, { x: 0.51, y: 0.72 }],
      ],
    },
  };
}

const handwritten = {
  coordinateSpace: { width: 520, height: 220 },
  strokes: [
    [{ x: 0.18, y: 0.76 }, { x: 0.2, y: 0.39 }, { x: 0.21, y: 0.22 }, { x: 0.46, y: 0.25 }, { x: 0.51, y: 0.35 }, { x: 0.32, y: 0.49 }, { x: 0.21, y: 0.48 }],
    [{ x: 0.34, y: 0.48 }, { x: 0.43, y: 0.57 }, { x: 0.52, y: 0.69 }, { x: 0.59, y: 0.78 }],
    [{ x: 0.48, y: 0.66 }, { x: 0.59, y: 0.57 }, { x: 0.69, y: 0.61 }, { x: 0.79, y: 0.55 }, { x: 0.85, y: 0.43 }],
  ],
};

export function verificationFixture(mode = "typed") {
  if (!["typed", "handwritten"].includes(mode)) throw new Error("INVALID_PREVIEW_FIXTURE");
  return {
    provenance: PREVIEW_PROVENANCE,
    targetReference: "dev-target-fixture",
    mark: { version: 1, mode, typed: "Mori", handwritten },
  };
}
