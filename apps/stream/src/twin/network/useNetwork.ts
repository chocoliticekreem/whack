import { useMemo } from "react";
import type { TwinGeometry } from "../config";
import { useGeometry } from "../scene/useGeometry";
import { buildNetwork } from "./graph";
import type { RoadNetwork } from "./types";

const cache = new WeakMap<TwinGeometry, RoadNetwork>();

/** One network per geometry object, shared by roads, pavements, traffic and pedestrians. */
export function getNetwork(geometry: TwinGeometry): RoadNetwork {
  let net = cache.get(geometry);
  if (!net) {
    net = buildNetwork(geometry);
    cache.set(geometry, net);
  }
  return net;
}

export function useNetwork(): RoadNetwork {
  const geometry = useGeometry();
  return useMemo(() => getNetwork(geometry), [geometry]);
}
