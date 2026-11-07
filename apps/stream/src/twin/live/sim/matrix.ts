/** Writes a column-major instance matrix: yaw about +y, scale (sx, sy, sx), translation (x, y, z). */
export function writeYawMatrix(
  te: ArrayLike<number> & { [i: number]: number },
  o: number,
  x: number,
  y: number,
  z: number,
  yaw: number,
  sx = 1,
  sy = 1,
) {
  const c = Math.cos(yaw) * sx;
  const s = Math.sin(yaw) * sx;
  te[o] = c;
  te[o + 1] = 0;
  te[o + 2] = -s;
  te[o + 3] = 0;
  te[o + 4] = 0;
  te[o + 5] = sy;
  te[o + 6] = 0;
  te[o + 7] = 0;
  te[o + 8] = s;
  te[o + 9] = 0;
  te[o + 10] = c;
  te[o + 11] = 0;
  te[o + 12] = x;
  te[o + 13] = y;
  te[o + 14] = z;
  te[o + 15] = 1;
}
