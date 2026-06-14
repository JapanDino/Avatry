import { create } from "zustand";
import {
  type Calibration,
  type Gender,
  type Measurements,
  measurementsToMorphs,
  neutralMeasurements,
} from "./calibration";
import type { Garment } from "./fit";

/**
 * Морфы тела (имена = morph targets в GLB, см. pipeline/morphs.py).
 * Морфы вычисляются из мерок (см. calibration.ts), но их можно поправить вручную
 * в «тонкой настройке».
 */
export type MorphName =
  | "height"
  | "weight"
  | "muscle"
  | "chest"
  | "waist"
  | "hips"
  | "shoulders"
  | "neck"
  | "arm"
  | "thigh";

export const MORPH_ORDER: MorphName[] = [
  "height",
  "weight",
  "muscle",
  "chest",
  "waist",
  "hips",
  "shoulders",
  "neck",
  "arm",
  "thigh",
];

export const MORPH_LABELS: Record<MorphName, string> = {
  height: "Рост",
  weight: "Полнота",
  muscle: "Мускулатура",
  chest: "Грудь",
  waist: "Талия",
  hips: "Бёдра",
  shoulders: "Плечи",
  neck: "Шея",
  arm: "Бицепс",
  thigh: "Бедро",
};

const ZERO = Object.fromEntries(MORPH_ORDER.map((m) => [m, 0])) as Record<
  MorphName,
  number
>;

const FALLBACK_MEASUREMENTS: Measurements = {
  height: 165,
  weight: 60,
  chest: 88,
  waist: 72,
  hips: 92,
};

interface AvatarState {
  calibration: Calibration | null;
  gender: Gender;
  measurements: Measurements;
  morphs: Record<MorphName, number>;
  showClothing: boolean;
  garment: Garment | null;
  selectedSize: string | null;

  setCalibration: (cal: Calibration) => void;
  setGender: (g: Gender) => void;
  setMeasurement: (key: keyof Measurements, value: number) => void;
  setMorph: (name: MorphName, value: number) => void;
  resetMeasurements: () => void;
  setShowClothing: (v: boolean) => void;
  setGarment: (g: Garment | null) => void;
  setSelectedSize: (label: string | null) => void;
}

function recompute(
  cal: Calibration | null,
  gender: Gender,
  m: Measurements,
): Record<MorphName, number> {
  if (!cal) return { ...ZERO };
  return measurementsToMorphs(cal, gender, m);
}

export const useAvatarStore = create<AvatarState>((set) => ({
  calibration: null,
  gender: "female",
  measurements: FALLBACK_MEASUREMENTS,
  morphs: { ...ZERO },
  showClothing: true,
  garment: null,
  selectedSize: null,

  setCalibration: (cal) =>
    set((s) => {
      const measurements = neutralMeasurements(cal, s.gender);
      return {
        calibration: cal,
        measurements,
        morphs: recompute(cal, s.gender, measurements),
      };
    }),

  setGender: (gender) =>
    set((s) => {
      const measurements = s.calibration
        ? neutralMeasurements(s.calibration, gender)
        : s.measurements;
      return {
        gender,
        measurements,
        morphs: recompute(s.calibration, gender, measurements),
      };
    }),

  setMeasurement: (key, value) =>
    set((s) => {
      const measurements = { ...s.measurements, [key]: value };
      return {
        measurements,
        morphs: recompute(s.calibration, s.gender, measurements),
      };
    }),

  setMorph: (name, value) =>
    set((s) => ({ morphs: { ...s.morphs, [name]: value } })),

  resetMeasurements: () =>
    set((s) => {
      const measurements = s.calibration
        ? neutralMeasurements(s.calibration, s.gender)
        : FALLBACK_MEASUREMENTS;
      return {
        measurements,
        morphs: recompute(s.calibration, s.gender, measurements),
      };
    }),

  setShowClothing: (v) => set({ showClothing: v }),
  setGarment: (g) => set({ garment: g, selectedSize: null }),
  setSelectedSize: (label) => set({ selectedSize: label }),
}));
