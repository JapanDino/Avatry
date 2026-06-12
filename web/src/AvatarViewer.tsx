import { useEffect, useMemo } from "react";
import { useGLTF } from "@react-three/drei";
import * as THREE from "three";
import { useAvatarStore } from "./store";

import type { Gender } from "./calibration";

const urls = (gender: Gender) => ({
  body: `/models/body_${gender}.glb`,
  top: `/models/top_${gender}.glb`,
  shorts: `/models/shorts_${gender}.glb`,
});

/**
 * Процедурная карта нормалей ткани (плетение basketweave). Генерируется один раз,
 * тайлится по UV вещи — даёт микрорельеф хлопка без загрузки текстур.
 */
let _fabricNormal: THREE.DataTexture | null = null;
function fabricNormalTexture(): THREE.DataTexture {
  if (_fabricNormal) return _fabricNormal;
  const size = 256;
  const threads = 28;
  const strength = 1.2;
  const data = new Uint8Array(size * size * 4);
  // гладкое плетение: «яичная решётка» (sin*sin) — без резких градиентов
  const f = (Math.PI * 2 * threads) / size;
  const height = (x: number, y: number) =>
    Math.sin(x * f) * Math.sin(y * f) * 0.5 + 0.5;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const hL = height((x - 1 + size) % size, y);
      const hR = height((x + 1) % size, y);
      const hD = height(x, (y - 1 + size) % size);
      const hU = height(x, (y + 1) % size);
      // нормаль = normalize(-dh/dx, -dh/dy, 1)
      let nx = (hL - hR) * strength;
      let ny = (hD - hU) * strength;
      let nz = 1.0;
      const len = Math.hypot(nx, ny, nz) || 1;
      nx /= len;
      ny /= len;
      nz /= len;
      const i = (y * size + x) * 4;
      data[i] = (nx * 0.5 + 0.5) * 255;
      data[i + 1] = (ny * 0.5 + 0.5) * 255;
      data[i + 2] = (nz * 0.5 + 0.5) * 255;
      data[i + 3] = 255;
    }
  }
  const tex = new THREE.DataTexture(data, size, size, THREE.RGBAFormat);
  tex.wrapS = tex.wrapT = THREE.RepeatWrapping;
  tex.repeat.set(8, 8);
  tex.generateMipmaps = true;
  tex.minFilter = THREE.LinearMipmapLinearFilter;
  tex.magFilter = THREE.LinearFilter;
  tex.anisotropy = 8;
  tex.needsUpdate = true;
  _fabricNormal = tex;
  return tex;
}

/**
 * Загружает один GLB и применяет влияния морф-таргетов из стора по имени.
 * skin=true заменяет материал на нейтральную кожу (для тела); для одежды
 * сохраняется материал из GLB.
 */
function MorphedModel({ url, skin = false }: { url: string; skin?: boolean }) {
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
          // одежда: материал из GLB + тканевая нормаль (плетение)
          const mat = m.material as THREE.MeshStandardMaterial;
          if (mat && "envMapIntensity" in mat) {
            mat.envMapIntensity = 1.0;
            mat.roughness = 0.9;
            mat.metalness = 0.0;
            mat.color.setRGB(0.5, 0.52, 0.58); // читаемый серый
            mat.normalMap = fabricNormalTexture();
            mat.normalScale = new THREE.Vector2(0.35, 0.35);
            mat.needsUpdate = true;
          }
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

  return <primitive object={scene} />;
}

for (const g of ["female", "male"] as Gender[]) {
  const u = urls(g);
  useGLTF.preload(u.body);
  useGLTF.preload(u.top);
  useGLTF.preload(u.shorts);
}

/** Сцена: свет, тело и базовая одежда (верх + шорты) выбранного пола. */
export function AvatarScene() {
  const showClothing = useAvatarStore((s) => s.showClothing);
  const gender = useAvatarStore((s) => s.gender);
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
      {showClothing && <MorphedModel key={`${gender}-top`} url={u.top} />}
      {showClothing && <MorphedModel key={`${gender}-shorts`} url={u.shorts} />}
    </group>
  );
}
