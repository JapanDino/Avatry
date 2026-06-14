import type { Measurements } from "./calibration";

/** Профиль посадки изделия — задаёт целевой запас (ease) по груди. */
export type FitProfile = "slim" | "regular" | "relaxed" | "oversize";

export interface GarmentSize {
  label: string;
  garment: {
    chest: number;
    waist: number;
    hips: number;
    length?: number;
    sleeve?: number;
  };
}

export interface Garment {
  id: string;
  title: string;
  type: string;
  gender: "male" | "female" | "unisex";
  fit: FitProfile;
  elasticity: number; // 0..1
  glb: string;
  sizes: GarmentSize[];
}

export type FitClass = "tight" | "good" | "loose";
export type Zone = "chest" | "waist" | "hips";
export const ZONES: Zone[] = ["chest", "waist", "hips"];
export const ZONE_LABELS: Record<Zone, string> = {
  chest: "Грудь",
  waist: "Талия",
  hips: "Бёдра",
};
const ZONE_PREP: Record<Zone, string> = {
  chest: "в груди",
  waist: "в талии",
  hips: "в бёдрах",
};

// Профиль посадки: целевой запас по груди (ideal) и порог «свободно» (loose), см.
// «Тесно» отдельно и не зависит от профиля — это физическая носибельность.
// Числа согласованы с практикой кроя (wearing ease) и размерным шагом EN 13402-3
// (буквенные размеры = диапазоны обхвата груди). См. docs/specs/stage-4.md.
const PROFILE: Record<FitProfile, { ideal: number; loose: number }> = {
  slim: { ideal: 4, loose: 12 },
  regular: { ideal: 10, loose: 18 },
  relaxed: { ideal: 15, loose: 28 },
  oversize: { ideal: 18, loose: 40 },
};
const TIGHT_MIN = 2; // effective-запас (см) ниже которого вещь реально мала
const STRETCH_ALLOWANCE = 7; // см при elasticity = 1

// Какая зона решает посадку для типа вещи (остальные — второстепенные/драпировка).
const PRIMARY_ZONE: Record<string, Zone> = {
  tshirt: "chest",
  hoodie: "chest",
  shirt: "chest",
  dress: "chest",
  jacket: "chest",
  pants: "waist",
  jeans: "waist",
  shorts: "waist",
  skirt: "hips",
};

export interface ZoneFit {
  zone: Zone;
  ease: number; // обхват изделия − тела (см)
  effective: number; // с поправкой на эластичность
  cls: FitClass;
  primary: boolean;
}

export interface SizeFit {
  label: string;
  zones: ZoneFit[];
  cost: number;
}

export interface FitResult {
  perSize: SizeFit[];
  recommended: string | null;
  explanation: string;
}

function primaryZone(g: Garment): Zone {
  return PRIMARY_ZONE[g.type] ?? "chest";
}

function classify(
  effective: number,
  loose: number,
): FitClass {
  if (effective < TIGHT_MIN) return "tight"; // физически мало
  if (effective > loose) return "loose";
  return "good";
}

function sizeFit(size: GarmentSize, body: Measurements, g: Garment): SizeFit {
  const p = PROFILE[g.fit];
  const give = g.elasticity * STRETCH_ALLOWANCE;
  const prim = primaryZone(g);

  const zones: ZoneFit[] = ZONES.map((z) => {
    const ease = size.garment[z] - body[z];
    const effective = ease + give;
    return {
      zone: z,
      ease,
      effective,
      cls: classify(effective, p.loose),
      primary: z === prim,
    };
  });

  // стоимость = близость главной зоны к идеалу профиля
  const primZone = zones.find((z) => z.primary)!;
  const cost = Math.abs(primZone.effective - p.ideal);
  return { label: size.label, zones, cost };
}

const round = (v: number) => Math.round(v);

export function computeFit(garment: Garment, body: Measurements): FitResult {
  const perSize = garment.sizes.map((s) => sizeFit(s, body, garment));

  // Кандидаты — размеры, где главная зона не «тесно». Из них — ближайший к идеалу
  // (при равенстве — меньший). Если все тесны — берём самый большой и честно
  // говорим, что мал.
  const fits = perSize.filter((s) => {
    const prim = s.zones.find((z) => z.primary)!;
    return prim.cls !== "tight";
  });

  // при равной близости к идеалу: oversize/relaxed → больший размер («size up»),
  // slim/regular → меньший (прилегающий)
  const preferLarger = garment.fit === "oversize" || garment.fit === "relaxed";
  let best: SizeFit | null = null;
  for (const sf of fits) {
    if (!best) best = sf;
    else if (preferLarger ? sf.cost <= best.cost + 1e-6 : sf.cost < best.cost - 1e-6)
      best = sf;
  }
  const allTight = best === null;
  if (!best) best = perSize[perSize.length - 1] ?? null;

  let explanation = "";
  if (best) {
    const prim = best.zones.find((z) => z.primary)!;
    if (allTight) {
      explanation = `Даже ${best.label} маловат — этой вещи, похоже, нет в вашем размере.`;
    } else {
      const tail = prim.cls === "loose" ? " — сидит свободно" : "";
      explanation = `Ваш размер — ${best.label}: по груди запас ~${round(prim.ease)} см${tail}.`;
    }
  }

  return { perSize, recommended: best ? best.label : null, explanation };
}

export { ZONE_PREP };
