import { useEffect, useMemo } from "react";
import { useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { useAvatarStore } from "./store";
import { computeFit } from "./fit";

import type { Gender } from "./calibration";

const bodyUrl = (g: Gender) => `/models/body_${g}.glb`;
const shortsUrl = (g: Gender) => `/models/shorts_${g}.glb`;
// верх — отдельный меш на размер (реальная посадка S/M/L), а не масштаб одного
const topUrl = (g: Gender, size: string) => `/models/top_${g}_${size}.glb`;

/**
 * CC0-текстуры ткани (cotton_jersey, Poly Haven): diffuse + normal + roughness.
 * Тайлятся по UV вещи; см. assets/SOURCES.md.
 */
const _texLoader = new THREE.TextureLoader();
function fabricTex(file: string, srgb: boolean, repeat: number): THREE.Texture {
  const t = _texLoader.load(`/textures/cotton_jersey/${file}`);
  t.wrapS = t.wrapT = THREE.RepeatWrapping;
  t.repeat.set(repeat, repeat);
  t.anisotropy = 8;
  if (srgb) t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

/**
 * Физический материал ткани: настоящие CC0-карты трикотажа (diffuse/normal/
 * roughness) + sheen (мягкая ворсистость хлопка). Один экземпляр на всю одежду.
 */
let _fabricMat: THREE.MeshPhysicalMaterial | null = null;
function fabricMaterial(): THREE.MeshPhysicalMaterial {
  if (_fabricMat) return _fabricMat;
  const REPEAT = 6;
  const m = new THREE.MeshPhysicalMaterial({
    map: fabricTex("diff.jpg", true, REPEAT),
    normalMap: fabricTex("nor_gl.jpg", false, REPEAT),
    roughnessMap: fabricTex("rough.jpg", false, REPEAT),
    color: new THREE.Color(0.55, 0.58, 0.66), // тонировка трикотажа
    roughness: 1.0,
    metalness: 0.0,
    sheen: 1.0,
    sheenRoughness: 0.8,
    sheenColor: new THREE.Color(0xffffff),
    envMapIntensity: 1.0,
  });
  m.normalScale = new THREE.Vector2(0.8, 0.8);
  _fabricMat = m;
  return m;
}

/**
 * Загружает один GLB и применяет влияния морф-таргетов из стора по имени.
 * skin=true заменяет материал на нейтральную кожу (для тела); для одежды
 * сохраняется материал из GLB.
 */
/** Масштаб одежды под выбранный размер (обхват/длина), пивот по высоте плеч. */
interface SizeScale {
  girth: number;
  length: number;
  pivotY: number;
}

/** Цвета зон карты посадки (грудь/талия/бёдра). null = карта выключена. */
type ZoneColors = Record<"chest" | "waist" | "hips", THREE.Color> | null;

let _fitMapMat: THREE.MeshStandardMaterial | null = null;
function fitMapMaterial(): THREE.MeshStandardMaterial {
  if (_fitMapMat) return _fitMapMat;
  _fitMapMat = new THREE.MeshStandardMaterial({
    vertexColors: true,
    roughness: 0.85,
    metalness: 0.0,
    envMapIntensity: 0.6,
  });
  return _fitMapMat;
}

/** Раскрасить вершины одежды по зонам (по высоте Y) цветами посадки. */
function applyFitColors(mesh: THREE.Mesh, colors: ZoneColors): void {
  const geo = mesh.geometry;
  const pos = geo.getAttribute("position");
  if (!pos) return;
  let ymin = Infinity;
  let ymax = -Infinity;
  for (let i = 0; i < pos.count; i++) {
    const y = pos.getY(i);
    if (y < ymin) ymin = y;
    if (y > ymax) ymax = y;
  }
  const span = ymax - ymin || 1;
  const arr = new Float32Array(pos.count * 3);
  const tmp = new THREE.Color();
  for (let i = 0; i < pos.count; i++) {
    const f = (pos.getY(i) - ymin) / span; // 0 низ (бёдра), 1 верх (грудь)
    const c = colors![f > 0.6 ? "chest" : f > 0.34 ? "waist" : "hips"];
    tmp.copy(c);
    arr[i * 3] = tmp.r;
    arr[i * 3 + 1] = tmp.g;
    arr[i * 3 + 2] = tmp.b;
  }
  geo.setAttribute("color", new THREE.BufferAttribute(arr, 3));
}

function MorphedModel({
  url,
  skin = false,
  sizeScale,
  fitColors = null,
}: {
  url: string;
  skin?: boolean;
  sizeScale?: SizeScale;
  fitColors?: ZoneColors;
}) {
  const { scene } = useGLTF(url);
  const morphs = useAvatarStore((s) => s.morphs);

  const meshes = useMemo(() => {
    const found: THREE.Mesh[] = [];
    scene.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh && m.morphTargetDictionary) {
        if (skin) {
          m.material = new THREE.MeshStandardMaterial({
            color: "#c9a88f",
            roughness: 0.62,
            metalness: 0.0,
            envMapIntensity: 0.9,
          });
        } else {
          // одежда: физический материал ткани со sheen (ворсистость хлопка)
          m.material = fabricMaterial();
        }
        m.castShadow = true;
        m.receiveShadow = true;
        m.frustumCulled = false;
        found.push(m);
      }
    });
    return found;
  }, [scene, skin]);

  useEffect(() => {
    for (const mesh of meshes) {
      const dict = mesh.morphTargetDictionary;
      const infl = mesh.morphTargetInfluences;
      if (!dict || !infl) continue;
      for (const [name, value] of Object.entries(morphs)) {
        const idx = dict[name];
        if (idx !== undefined) infl[idx] = value;
      }
    }
  }, [meshes, morphs]);

  // карта посадки: раскраска по зонам ↔ обычная ткань
  useEffect(() => {
    if (skin) return;
    for (const mesh of meshes) {
      if (fitColors) {
        applyFitColors(mesh, fitColors);
        mesh.material = fitMapMaterial();
      } else {
        mesh.material = fabricMaterial();
      }
    }
  }, [meshes, skin, fitColors]);

  if (sizeScale) {
    // масштаб обхвата вокруг вертикальной оси (X,Z) + длины (Y) с пивотом у плеч
    const { girth, length, pivotY } = sizeScale;
    return (
      <group position={[0, pivotY * (1 - length), 0]} scale={[girth, length, girth]}>
        <primitive object={scene} />
      </group>
    );
  }
  return <primitive object={scene} />;
}

for (const g of ["female", "male"] as Gender[]) {
  useGLTF.preload(bodyUrl(g));
  useGLTF.preload(shortsUrl(g));
}

/** Активный размер: выбранный пользователем или рекомендованный. */
function useActiveSize(): string | null {
  const garment = useAvatarStore((s) => s.garment);
  const measurements = useAvatarStore((s) => s.measurements);
  const selectedSize = useAvatarStore((s) => s.selectedSize);
  return useMemo(() => {
    if (!garment) return null;
    if (selectedSize) return selectedSize;
    return computeFit(garment, measurements).recommended;
  }, [garment, measurements, selectedSize]);
}

/**
 * Доп. масштаб длины верха по размеру (обхват уже реальный в per-size GLB —
 * задача 4.3). Длина варьируется немного по размерной таблице.
 */
function useTopLengthScale(): SizeScale | undefined {
  const garment = useAvatarStore((s) => s.garment);
  const measurements = useAvatarStore((s) => s.measurements);
  const selectedSize = useAvatarStore((s) => s.selectedSize);
  const gender = useAvatarStore((s) => s.gender);

  return useMemo(() => {
    if (!garment) return undefined;
    const fit = computeFit(garment, measurements);
    const mid = garment.sizes[Math.floor(garment.sizes.length / 2)];
    const active = garment.sizes.find(
      (s) => s.label === (selectedSize ?? fit.recommended),
    );
    if (!active || !mid) return undefined;
    const clamp = (v: number, lo: number, hi: number) =>
      Math.max(lo, Math.min(hi, v));
    const length = clamp(
      (active.garment.length ?? 1) / (mid.garment.length ?? 1),
      0.94,
      1.1,
    );
    const pivotY = gender === "male" ? 1.45 : 1.36;
    return { girth: 1, length, pivotY };
  }, [garment, measurements, selectedSize, gender]);
}

const FIT_CLASS_COLOR = {
  tight: new THREE.Color("#e2574c"),
  good: new THREE.Color("#4caf6a"),
  loose: new THREE.Color("#5b8def"),
};

/** Цвета зон карты посадки для активного размера (или null, если карта выкл.). */
function useFitColors(): ZoneColors {
  const garment = useAvatarStore((s) => s.garment);
  const measurements = useAvatarStore((s) => s.measurements);
  const selectedSize = useAvatarStore((s) => s.selectedSize);
  const showFitMap = useAvatarStore((s) => s.showFitMap);

  return useMemo(() => {
    if (!showFitMap || !garment) return null;
    const fit = computeFit(garment, measurements);
    const active =
      fit.perSize.find((s) => s.label === (selectedSize ?? fit.recommended)) ??
      null;
    if (!active) return null;
    const byZone = (z: "chest" | "waist" | "hips") =>
      FIT_CLASS_COLOR[active.zones.find((zf) => zf.zone === z)!.cls];
    return { chest: byZone("chest"), waist: byZone("waist"), hips: byZone("hips") };
  }, [showFitMap, garment, measurements, selectedSize]);
}

/** Сцена: свет, тело и базовая одежда (верх по размеру + шорты) выбранного пола. */
export function AvatarScene() {
  const showClothing = useAvatarStore((s) => s.showClothing);
  const gender = useAvatarStore((s) => s.gender);
  const garment = useAvatarStore((s) => s.garment);
  const topScale = useTopLengthScale();
  const fitColors = useFitColors();
  const activeSize = useActiveSize();

  // префетч всех размеров текущего пола — смена размера мгновенная
  useEffect(() => {
    if (!garment) return;
    for (const s of garment.sizes) useGLTF.preload(topUrl(gender, s.label));
  }, [garment, gender]);

  const topSrc = activeSize ? topUrl(gender, activeSize) : null;

  return (
    <group>
      {/* окружение (Environment) даёт мягкий заполняющий свет; добавляем ключевой
          направленный с тенями для объёма */}
      <directionalLight
        position={[3, 5, 4]}
        intensity={2.0}
        castShadow
        shadow-mapSize={[2048, 2048]}
        shadow-bias={-0.0004}
      >
        <orthographicCamera
          attach="shadow-camera"
          args={[-1.5, 1.5, 2.2, -0.2, 0.1, 12]}
        />
      </directionalLight>
      <MorphedModel key={`${gender}-body`} url={bodyUrl(gender)} skin />
      {showClothing && topSrc && (
        <MorphedModel
          key={`${gender}-${activeSize}-top`}
          url={topSrc}
          sizeScale={topScale}
          fitColors={fitColors}
        />
      )}
      {showClothing && (
        <MorphedModel key={`${gender}-shorts`} url={shortsUrl(gender)} />
      )}
    </group>
  );
}
