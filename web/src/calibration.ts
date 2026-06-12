import type { MorphName } from "./store";

/** Пол. MakeHuman-ассеты и калибровка раздельны по полу. */
export type Gender = "female" | "male";

/** Мерки пользователя (см, вес — кг). */
export interface Measurements {
  height: number;
  weight: number;
  chest: number;
  waist: number;
  hips: number;
}

interface GenderCalibration {
  neutral: { height: number; chest: number; waist: number; hips: number };
  perUnit: { height: number; chest: number; waist: number; hips: number };
}

export type Calibration = Record<Gender, GenderCalibration>;

/** Референсный вес при BMI 22 для роста (см) — нейтраль ползунка веса. */
export function referenceWeight(heightCm: number): number {
  const h = heightCm / 100;
  return 22 * h * h;
}

/** Дефолтные мерки пола = нейтральное тело (аватар стартует «средним»). */
export function neutralMeasurements(
  cal: Calibration,
  gender: Gender,
): Measurements {
  const n = cal[gender].neutral;
  return {
    height: Math.round(n.height),
    weight: Math.round(referenceWeight(n.height)),
    chest: Math.round(n.chest),
    waist: Math.round(n.waist),
    hips: Math.round(n.hips),
  };
}

const clamp = (v: number, lo: number, hi: number) =>
  Math.max(lo, Math.min(hi, v));

/**
 * Мерки → веса морфов. Линейно: influence = (мерка − neutral) / perUnit.
 * Вес — через отклонение от BMI-референса. Вторичные морфы (muscle, shoulders,
 * neck, arm, thigh) остаются 0 — их при желании правят в «тонкой настройке».
 */
export function measurementsToMorphs(
  cal: Calibration,
  gender: Gender,
  m: Measurements,
): Record<MorphName, number> {
  const c = cal[gender];
  const inf = (val: number, neutral: number, per: number) =>
    per !== 0 ? clamp((val - neutral) / per, -1.2, 1.6) : 0;

  const weightRef = referenceWeight(m.height);
  const weightInf = clamp((m.weight - weightRef) / 16, -1.2, 1.6);

  return {
    height: inf(m.height, c.neutral.height, c.perUnit.height),
    chest: inf(m.chest, c.neutral.chest, c.perUnit.chest),
    waist: inf(m.waist, c.neutral.waist, c.perUnit.waist),
    hips: inf(m.hips, c.neutral.hips, c.perUnit.hips),
    weight: weightInf,
    muscle: 0,
    shoulders: 0,
    neck: 0,
    arm: 0,
    thigh: 0,
  };
}
