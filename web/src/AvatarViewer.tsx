import { useEffect, useMemo } from "react";
import { useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { useAvatarStore } from "./store";
import { computeFit } from "./fit";

import type { Gender } from "./calibration";

const urls = (gender: Gender) => ({
  body: `/models/body_${gender}.glb`,
  top: `/models/top_${gender}.glb`,
  shorts: `/models/shorts_${gender}.glb`,
});

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

function MorphedModel({
  url,
  skin = false,
  sizeScale,
}: {
  url: string;
  skin?: boolean;
  sizeScale?: SizeScale;
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
  const u = urls(g);
  useGLTF.preload(u.body);
  useGLTF.preload(u.top);
  useGLTF.preload(u.shorts);
}

/**
 * Масштаб верха под выбранный размер относительно рекомендованного: больше размер
 * → шире/длиннее (свободнее), меньше → ближе к телу. Так 3D-вещь меняется при
 * смене размера, хотя GLB один (полноценные per-size меши — задача 4.3).
 */
function useTopSizeScale(): SizeScale | undefined {
  const garment = useAvatarStore((s) => s.garment);
  const measurements = useAvatarStore((s) => s.measurements);
  const selectedSize = useAvatarStore((s) => s.selectedSize);
  const gender = useAvatarStore((s) => s.gender);

  return useMemo(() => {
    if (!garment) return undefined;
    const fit = computeFit(garment, measurements);
    const ref = garment.sizes.find((s) => s.label === fit.recommended);
    const active = garment.sizes.find(
      (s) => s.label === (selectedSize ?? fit.recommended),
    );
    if (!ref || !active) return undefined;
    const clamp = (v: number, lo: number, hi: number) =>
      Math.max(lo, Math.min(hi, v));
    const girth = clamp(active.garment.chest / ref.garment.chest, 0.9, 1.18);
    const length = clamp(
      (active.garment.length ?? 1) / (ref.garment.length ?? 1),
      0.94,
      1.12,
    );
    const pivotY = gender === "male" ? 1.45 : 1.36; // высота плеч, м
    return { girth, length, pivotY };
  }, [garment, measurements, selectedSize, gender]);
}

/** Сцена: свет, тело и базовая одежда (верх + шорты) выбранного пола. */
export function AvatarScene() {
  const showClothing = useAvatarStore((s) => s.showClothing);
  const gender = useAvatarStore((s) => s.gender);
  const topScale = useTopSizeScale();
  const u = urls(gender);
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
      <MorphedModel key={`${gender}-body`} url={u.body} skin />
      {showClothing && (
        <MorphedModel key={`${gender}-top`} url={u.top} sizeScale={topScale} />
      )}
      {showClothing && <MorphedModel key={`${gender}-shorts`} url={u.shorts} />}
    </group>
  );
}
