import { Suspense, useEffect } from "react";
import { Canvas } from "@react-three/fiber";
import {
  ContactShadows,
  Environment,
  Lightformer,
  OrbitControls,
  SoftShadows,
} from "@react-three/drei";
import {
  Bloom,
  EffectComposer,
  N8AO,
  SMAA,
  ToneMapping,
} from "@react-three/postprocessing";
import { ToneMappingMode } from "postprocessing";
import { AvatarScene } from "./AvatarViewer";
import { MeasurementForm } from "./MeasurementForm";
import { useAvatarStore } from "./store";
import type { Calibration } from "./calibration";

/**
 * Этап 1: просмотрщик параметрического тела с базовой одеждой.
 * Студийное окружение + контактные тени + пост-обработка (AO, bloom, ACES).
 * `flat` отключает тонмаппинг рендерера — его делает ToneMapping в пост-стеке.
 */
export default function App() {
  const setCalibration = useAvatarStore((s) => s.setCalibration);
  useEffect(() => {
    fetch("/calibration.json")
      .then((r) => r.json())
      .then((cal: Calibration) => setCalibration(cal))
      .catch((e) => console.error("calibration load failed", e));
  }, [setCalibration]);

  return (
    <>
      <Canvas
        flat
        shadows
        dpr={[1, 2]}
        gl={{ antialias: false }}
        camera={{ position: [0, 1.0, 3.2], fov: 40 }}
      >
        <color attach="background" args={["#202028"]} />
        <SoftShadows size={24} samples={12} focus={0.9} />

        <Suspense fallback={null}>
          <AvatarScene />
        </Suspense>

        {/* Процедурное студийное окружение: мягкие софт-боксы, без сетевых HDRI. */}
        <Environment resolution={256}>
          <Lightformer
            intensity={1.6}
            position={[0, 2.5, 1.5]}
            scale={[6, 3, 1]}
            color="#ffffff"
          />
          <Lightformer
            intensity={0.8}
            position={[-3, 1, 1]}
            scale={[3, 4, 1]}
            color="#bcd0ff"
          />
          <Lightformer
            intensity={0.7}
            position={[3, 1, -1]}
            scale={[3, 4, 1]}
            color="#ffd9bc"
          />
        </Environment>

        <ContactShadows
          position={[0, 0.001, 0]}
          opacity={0.55}
          scale={6}
          blur={2.4}
          far={2.2}
          resolution={1024}
          color="#000000"
        />

        <OrbitControls
          makeDefault
          enableDamping
          target={[0, 0.9, 0]}
          minDistance={1.2}
          maxDistance={6}
        />

        {/* multisampling=0: MSAA выключен (конфликтует с depth-based N8AO),
            сглаживание делает SMAA */}
        <EffectComposer multisampling={0}>
          {/* мягкое затенение в складках/контактах */}
          {/* малый радиус: AO ловит складки/контакты, но не «затемняет»
              всю одежду, лежащую в ~1 см от тела */}
          <N8AO aoRadius={0.05} intensity={1.1} distanceFalloff={1.0} />
          {/* лёгкий блик на светлых местах */}
          <Bloom
            intensity={0.12}
            luminanceThreshold={0.9}
            luminanceSmoothing={0.25}
            mipmapBlur
          />
          <ToneMapping mode={ToneMappingMode.ACES_FILMIC} />
          <SMAA />
        </EffectComposer>
      </Canvas>
      <MeasurementForm />
    </>
  );
}
