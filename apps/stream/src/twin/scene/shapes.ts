import * as THREE from "three";
import type { Ring } from "../config";

/** Local [x, z] ring → a Shape in XY that, after rotateX(-π/2), lands on the XZ ground plane. */
export function ringToShape(ring: Ring): THREE.Shape {
  const shape = new THREE.Shape();
  ring.forEach(([x, z], i) => {
    if (i === 0) shape.moveTo(x, -z);
    else shape.lineTo(x, -z);
  });
  shape.closePath();
  return shape;
}
