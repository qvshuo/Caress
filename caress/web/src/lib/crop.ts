import type { PercentCrop } from "react-image-crop";
import type { Box } from "./types";

export function percentCrop(
  box: Box,
  width: number,
  height: number,
): PercentCrop {
  return {
    unit: "%",
    x: (box[0] / width) * 100,
    y: (box[1] / height) * 100,
    width: (box[2] / width) * 100,
    height: (box[3] / height) * 100,
  };
}

// Backend crops require whole pixels and an exact 2:3 ratio, even after touch resizing.
export function cropBox(crop: PercentCrop, width: number, height: number): Box {
  const unit = Math.max(
    1,
    Math.min(
      Math.floor(width / 2),
      Math.floor(height / 3),
      Math.round(((crop.width / 100) * width) / 2),
      Math.round(((crop.height / 100) * height) / 3),
    ),
  );
  const w = unit * 2,
    h = unit * 3;
  return [
    Math.max(0, Math.min(width - w, Math.round((crop.x / 100) * width))),
    Math.max(0, Math.min(height - h, Math.round((crop.y / 100) * height))),
    w,
    h,
  ];
}
