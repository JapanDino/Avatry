import { Canvas } from "@react-three/fiber";
import { Grid, OrbitControls } from "@react-three/drei";

/**
 * Этап 0: пустая сцена-каркас.
 * Куб-плейсхолдер на сетке + орбитальная камера + свет.
 * На этапе 1 куб заменит <AvatarViewer> с телом из GLB.
 */
function PlaceholderScene() {
  return (
    <>
      {/* Свет: мягкий заполняющий + направленный ключевой. */}
      <ambientLight intensity={0.6} />
      <directionalLight position={[3, 5, 4]} intensity={1.4} castShadow />

      {/* Плейсхолдер на месте будущего аватара. */}
      <mesh position={[0, 0.5, 0]} castShadow>
        <boxGeometry args={[1, 1, 1]} />
        <meshStandardMaterial color="#7c5cff" />
      </mesh>

      {/* Пол-ориентир. */}
      <Grid
        args={[10, 10]}
        cellColor="#6f6f6f"
        sectionColor="#9d9d9d"
        infiniteGrid
        fadeDistance={25}
        position={[0, 0, 0]}
      />

      <OrbitControls makeDefault enableDamping target={[0, 0.5, 0]} />
    </>
  );
}

export default function App() {
  return (
    <Canvas shadows camera={{ position: [3, 2.5, 4], fov: 45 }}>
      <color attach="background" args={["#1a1a1f"]} />
      <PlaceholderScene />
    </Canvas>
  );
}
