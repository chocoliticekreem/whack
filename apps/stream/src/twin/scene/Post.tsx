"use client";

import {
  Bloom,
  BrightnessContrast,
  EffectComposer,
  HueSaturation,
  N8AO,
  SMAA,
  TiltShift2,
  ToneMapping,
  Vignette,
} from "@react-three/postprocessing";
import { useFrame } from "@react-three/fiber";
import { SMAAPreset, ToneMappingMode, type BloomEffect, type BrightnessContrastEffect, type HueSaturationEffect } from "postprocessing";
import { useMemo, useRef } from "react";
import { ATMOS } from "./palette";
import { fxOff, perf } from "./perf";

/**
 * One composer, multisampling stays 0 (4 blanks the canvas on this setup): N8AO ambient occlusion (full / half res per
 * tier), bloom on emissive > 1 only (night windows, sun glints), ACES, grade, vignette, SMAA for the edges.
 * `?tilt=1` adds a tilt-shift band; depth of field stays off.
 */
export function Post() {
  const bloom = useRef<BloomEffect>(null);
  const grade = useRef<BrightnessContrastEffect>(null);
  const saturation = useRef<HueSaturationEffect>(null);
  const tilt = useMemo(() => new URLSearchParams(window.location.search).get("tilt") === "1", []);
  const ao = fxOff("noao") ? "off" : perf.ao;
  const smaa = !fxOff("nosmaa");
  const glow = !fxOff("nobloom");

  useFrame(() => {
    if (bloom.current) bloom.current.intensity = ATMOS.bloom;
    if (grade.current) grade.current.brightness = ATMOS.brightness;
    if (saturation.current) saturation.current.saturation = ATMOS.saturation;
  });

  return (
    <EffectComposer multisampling={0}>
      {ao !== "off" && (
        <N8AO
          ref={(pass) => {
            // Why: the composer works in linear light and ToneMapping/sRGB conversion come later in the chain.
            if (pass) pass.configuration.gammaCorrection = false;
          }}
          aoRadius={6}
          distanceFalloff={2}
          intensity={2.5}
          quality={ao === "high" ? "medium" : "low"}
          halfRes
        />
      )}
      {glow && <Bloom ref={bloom} mipmapBlur intensity={1.1} luminanceThreshold={1} luminanceSmoothing={0.25} />}
      {tilt && <TiltShift2 blur={0.35} taper={0.6} samples={8} />}
      <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
      <HueSaturation ref={saturation} saturation={0.1} />
      <BrightnessContrast ref={grade} contrast={0.1} brightness={0.035} />
      <Vignette eskil={false} offset={0.2} darkness={0.5} />
      {smaa && <SMAA preset={SMAAPreset.HIGH} />}
    </EffectComposer>
  );
}
