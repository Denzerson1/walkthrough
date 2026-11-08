/**
 * A planar mirror drawing into a render target shared by every mirror.
 *
 * three's Reflector gives each mirror a buffer of its own; at the screen's
 * size, half-float and multisampled, five of them took over half a gigabyte
 * and the last ones came out black, and capped smaller they were
 * pixelated. A mirror draws its reflection in its onBeforeRender, just
 * before it is itself drawn, so one buffer serves them all, at the screen's
 * full size. The maths is three's Reflector (MIT).
 */
import * as THREE from 'three';

/** The reflection tinted, not overlay-blended, then tone mapped as the screen is. */
const SHADER = {
  vertexShader: /* glsl */ `
    uniform mat4 textureMatrix;
    varying vec4 vUv;
    #include <common>
    #include <logdepthbuf_pars_vertex>
    void main() {
      vUv = textureMatrix * vec4( position, 1.0 );
      gl_Position = projectionMatrix * modelViewMatrix * vec4( position, 1.0 );
      #include <logdepthbuf_vertex>
    }`,
  fragmentShader: /* glsl */ `
    uniform vec3 color;
    uniform sampler2D tDiffuse;
    varying vec4 vUv;
    #include <logdepthbuf_pars_fragment>
    void main() {
      #include <logdepthbuf_fragment>
      gl_FragColor = vec4( texture2DProj( tDiffuse, vUv ).rgb * color, 1.0 );
      #include <tonemapping_fragment>
      #include <colorspace_fragment>
    }`,
};

/** The one buffer every mirror draws into, half-float so lamps keep their range. */
export function mirrorTarget(width: number, height: number, samples: number): THREE.WebGLRenderTarget {
  return new THREE.WebGLRenderTarget(width, height, { samples, type: THREE.HalfFloatType });
}

/**
 * A mirror of `geometry`, its glass in its local xy plane looking along +z,
 * reflecting the scene into `target` each time it is drawn.
 */
export function createMirror(
  geometry: THREE.BufferGeometry,
  target: THREE.WebGLRenderTarget,
  color: THREE.Color,
  clipBias: number,
  maxSide: number,
): THREE.Mesh {
  const textureMatrix = new THREE.Matrix4();
  const material = new THREE.ShaderMaterial({
    name: 'MirrorShader',
    uniforms: {
      color: { value: color },
      tDiffuse: { value: target.texture },
      textureMatrix: { value: textureMatrix },
    },
    vertexShader: SHADER.vertexShader,
    fragmentShader: SHADER.fragmentShader,
  });
  const mirror = new THREE.Mesh(geometry, material);
  const plane = new THREE.Plane();
  const normal = new THREE.Vector3();
  const position = new THREE.Vector3();
  const eye = new THREE.Vector3();
  const rotation = new THREE.Matrix4();
  const lookAt = new THREE.Vector3();
  const clip = new THREE.Vector4();
  const view = new THREE.Vector3();
  const aim = new THREE.Vector3();
  const q = new THREE.Vector4();
  const cameras = new WeakMap<THREE.Camera, THREE.PerspectiveCamera>();

  const screen = new THREE.Vector2();
  mirror.onBeforeRender = (renderer, scene, camera) => {
    // The light meter's tiny render keeps the last reflection: drawing one
    // at its size would resize the shared buffer back and forth.
    if (renderer.getRenderTarget() !== null) return;
    let reflected = cameras.get(camera);
    if (!reflected) {
      reflected = (camera as THREE.PerspectiveCamera).clone();
      cameras.set(camera, reflected);
    }
    position.setFromMatrixPosition(mirror.matrixWorld);
    eye.setFromMatrixPosition(camera.matrixWorld);
    rotation.extractRotation(mirror.matrixWorld);
    normal.set(0, 0, 1).applyMatrix4(rotation);
    view.subVectors(position, eye);
    if (view.dot(normal) > 0) return; // seen from behind

    view.reflect(normal).negate().add(position);
    rotation.extractRotation(camera.matrixWorld);
    lookAt.set(0, 0, -1).applyMatrix4(rotation).add(eye);
    aim.subVectors(position, lookAt).reflect(normal).negate().add(position);
    reflected.position.copy(view);
    reflected.up.set(0, 1, 0).applyMatrix4(rotation).reflect(normal);
    reflected.lookAt(aim);
    reflected.far = (camera as THREE.PerspectiveCamera).far;
    reflected.updateMatrixWorld();
    reflected.projectionMatrix.copy(camera.projectionMatrix);

    textureMatrix.set(0.5, 0, 0, 0.5, 0, 0.5, 0, 0.5, 0, 0, 0.5, 0.5, 0, 0, 0, 1);
    textureMatrix.multiply(reflected.projectionMatrix);
    textureMatrix.multiply(reflected.matrixWorldInverse);
    textureMatrix.multiply(mirror.matrixWorld);

    // An oblique near plane on the glass (Lengyel): nothing behind it drawn.
    plane.setFromNormalAndCoplanarPoint(normal, position);
    plane.applyMatrix4(reflected.matrixWorldInverse);
    clip.set(plane.normal.x, plane.normal.y, plane.normal.z, plane.constant);
    const m = reflected.projectionMatrix.elements;
    q.set((Math.sign(clip.x) + m[8]) / m[0], (Math.sign(clip.y) + m[9]) / m[5], -1, (1 + m[10]) / m[14]);
    clip.multiplyScalar(2 / clip.dot(q));
    m[2] = clip.x;
    m[6] = clip.y;
    m[10] = clip.z + 1 - clipBias;
    m[14] = clip.w;

    mirror.visible = false;
    // The reflection as sharp as the screen it is seen on, up to `maxSide`.
    renderer.getDrawingBufferSize(screen);
    const scale = Math.min(1, maxSide / Math.max(screen.x, screen.y));
    const width = Math.round(screen.x * scale);
    const height = Math.round(screen.y * scale);
    if (target.width !== width || target.height !== height) target.setSize(width, height);
    renderer.setRenderTarget(target);
    renderer.state.buffers.depth.setMask(true);
    if (renderer.autoClear === false) renderer.clear();
    renderer.render(scene, reflected);
    renderer.setRenderTarget(null);
    mirror.visible = true;
  };
  return mirror;
}
