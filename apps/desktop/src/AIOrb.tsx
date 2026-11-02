import { useEffect, useRef, useState } from 'react'
import type { OrbState } from './orb-state'
import Icon from './Icon'

const vertex = `attribute vec2 position; void main(){gl_Position=vec4(position,0.,1.);}`
const fragment = `
precision highp float;
uniform vec2 resolution;
uniform vec2 pointer;
uniform float time;
uniform float activity;
uniform float online;
const float PI=3.14159265;
mat3 rotateY(float a){return mat3(cos(a),0.,sin(a),0.,1.,0.,-sin(a),0.,cos(a));}
mat3 rotateX(float a){return mat3(1.,0.,0.,0.,cos(a),-sin(a),0.,sin(a),cos(a));}
float sphere(vec3 ro,vec3 rd){float b=dot(ro,rd);float d=b*b-dot(ro,ro)+1.;return d<0.?-1.:-b-sqrt(d);}
void main(){
 vec2 uv=(gl_FragCoord.xy-.5*resolution)/min(resolution.x,resolution.y);
 mat3 camera=rotateY(pointer.x*.3)*rotateX(pointer.y*.22);
 vec3 ro=camera*vec3(0.,0.,4.8);
 vec3 rd=camera*normalize(vec3(uv,-1.62));
 float hit=sphere(ro,rd);
 vec3 cyan=mix(vec3(.22,.28,.31),vec3(.28,.78,.83),online);
 vec3 color=vec3(0.);
 float alpha=0.;
 if(hit>0.){
  vec3 p=ro+rd*hit;
  vec3 n=normalize(p);
  vec3 light=normalize(vec3(-.7,1.2,1.5));
  float diffuse=max(0.,dot(n,light));
  float fresnel=pow(1.-max(0.,dot(n,-rd)),3.);
  float spec=pow(max(0.,dot(reflect(-light,n),-rd)),32.);
  vec3 q=rotateY(time*.035)*p;
  float lat=asin(clamp(q.y,-1.,1.));
  float lon=atan(q.z,q.x);
  float latLine=1.-smoothstep(.012,.021,abs(sin(lat*20.)));
  float lonLine=1.-smoothstep(.012,.026,abs(sin(lon*28.)));
  float grid=max(latLine,lonLine)*.12;
  float field=sin(q.x*8.+sin(q.y*7.))*sin(q.z*9.-q.y*5.);
  float seam=1.-smoothstep(.015,.045,abs(field));
  float pulse=1.+activity*.13*sin(time*2.2);
  color=vec3(.025,.033,.038)+vec3(.055,.066,.073)*diffuse;
  color+=cyan*(grid*(.25+diffuse)+seam*.13+fresnel*.65)*pulse;
  color+=vec3(.65,.85,.89)*spec*.28;
  float notch=pow(max(0.,sin(lon*41.)*sin(lat*27.)),16.);
  color+=cyan*notch*.17;
  alpha=1.;
 }
 // Analytic 3D ring-plane intersections: depth-tested against the sphere.
 for(int i=0;i<3;i++){
  float index=float(i);
  vec3 normal=normalize(i==0?vec3(.13,.8,.6):i==1?vec3(.65,.5,-.65):vec3(.12,.18,1.));
  normal=rotateY(time*(i==2?.018:.025))*normal;
  float denom=dot(rd,normal);
  if(abs(denom)>.001){
   float t=-dot(ro,normal)/denom;
   vec3 p=ro+rd*t;
   float radius=1.16+index*.09;
   float distance=abs(length(p)-radius);
   float width=i==2?.005:.008;
   float ring=1.-smoothstep(width,width+.008,distance);
   if(t>0. && ring>0. && (hit<0. || t<hit)){
    float angle=atan(p.y,p.x);
    float sweep=.35+.65*pow(.5+.5*sin(angle-time*.12+index*2.),3.);
    float ticks=i==2?(.35+.65*step(.82,sin(angle*92.))):1.;
    vec3 ringColor=cyan*ring*sweep*ticks*.68;
    color=mix(color,ringColor,ring);
    alpha=max(alpha,ring);
   }
  }
 }
 // Gentle, bounded halo is lighting, not an additional animated object.
 float halo=exp(-length(uv)*length(uv)*28.)*.035*online;
 color+=cyan*halo;
 alpha=max(alpha,halo);
 gl_FragColor=vec4(color,alpha);
}`

export default function AIOrb({ state }: { state: OrbState }) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const stateRef = useRef(state)
  const rotation = useRef({ x: 0, y: 0, locked: false })
  const [fallback, setFallback] = useState(false)
  const [paused, setPaused] = useState(false)
  const pauseRef = useRef(false)
  useEffect(() => { stateRef.current = state }, [state])
  useEffect(() => { pauseRef.current = paused }, [paused])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const gl = canvas.getContext('webgl', { alpha: true, antialias: false, powerPreference: 'low-power', premultipliedAlpha: false })
    if (!gl) { setFallback(true); return }
    const compile = (type: number, source: string) => {
      const shader = gl.createShader(type)
      if (!shader) throw new Error('Shader allocation failed')
      gl.shaderSource(shader, source); gl.compileShader(shader)
      if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) { gl.deleteShader(shader); throw new Error('Shader compilation failed') }
      return shader
    }
    let program: WebGLProgram | null = null
    let buffer: WebGLBuffer | null = null
    const shaders: WebGLShader[] = []
    let frame = 0
    let disposed = false
    let lost = false
    try {
      shaders.push(compile(gl.VERTEX_SHADER, vertex))
      shaders.push(compile(gl.FRAGMENT_SHADER, fragment))
      program = gl.createProgram()
      if (!program) throw new Error('Program allocation failed')
      shaders.forEach(shader => gl.attachShader(program!, shader)); gl.linkProgram(program)
      if (!gl.getProgramParameter(program, gl.LINK_STATUS)) throw new Error('Shader link failed')
      gl.useProgram(program)
      buffer = gl.createBuffer(); gl.bindBuffer(gl.ARRAY_BUFFER, buffer)
      gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1,1,-1,-1,1,-1,1,1,-1,1,1]), gl.STATIC_DRAW)
      const position = gl.getAttribLocation(program, 'position')
      gl.enableVertexAttribArray(position); gl.vertexAttribPointer(position, 2, gl.FLOAT, false, 0, 0)
    } catch {
      shaders.forEach(shader => gl.deleteShader(shader))
      if (program) gl.deleteProgram(program)
      if (buffer) gl.deleteBuffer(buffer)
      setFallback(true)
      return
    }
    const uniforms = Object.fromEntries(['resolution','pointer','time','activity','online'].map(name => [name, gl.getUniformLocation(program!, name)]))
    const motion = window.matchMedia('(prefers-reduced-motion: reduce)')
    const observer = new ResizeObserver(() => {
      const bounds = canvas.getBoundingClientRect()
      // Maximum 720 px per edge and DPR 1.5: bounded fill rate on laptops.
      const scale = Math.min(window.devicePixelRatio || 1, 1.5, 720 / Math.max(bounds.width, bounds.height, 1))
      canvas.width = Math.max(1, Math.round(bounds.width * scale))
      canvas.height = Math.max(1, Math.round(bounds.height * scale))
    })
    observer.observe(canvas)
    let last = 0
    let elapsed = 0
    let previousState = ''
    let previousX = 0
    let previousY = 0
    let previousWidth = 0
    let previousHeight = 0
    const draw = (now: number) => {
      if (disposed || lost) return
      frame = requestAnimationFrame(draw)
      const delta = Math.min((now-last)/1000, .1)
      if (now-last < 1000/30) return
      last = now
      if (document.visibilityState === 'hidden') return
      const moving = !motion.matches && !pauseRef.current && stateRef.current !== 'offline'
      if (moving) elapsed += delta
      const x = motion.matches ? 0 : rotation.current.x
      const y = motion.matches ? 0 : rotation.current.y
      if (!moving && previousState === stateRef.current && previousX === x && previousY === y && previousWidth === canvas.width && previousHeight === canvas.height) {
        return // Static/reduced-motion scenes redraw only on state, view or size changes.
      }
      previousState = stateRef.current; previousX = x; previousY = y
      previousWidth = canvas.width; previousHeight = canvas.height
      gl.viewport(0,0,canvas.width,canvas.height)
      gl.uniform2f(uniforms.resolution,canvas.width,canvas.height)
      gl.uniform2f(uniforms.pointer,x,y)
      gl.uniform1f(uniforms.time,elapsed)
      gl.uniform1f(uniforms.activity,stateRef.current==='thinking'?1:stateRef.current==='responding'?.5:stateRef.current==='listening'?.7:0)
      gl.uniform1f(uniforms.online,stateRef.current==='offline'?0:1)
      gl.drawArrays(gl.TRIANGLES,0,6)
    }
    const onLost = (event: Event) => { event.preventDefault(); lost = true; cancelAnimationFrame(frame); setFallback(true) }
    canvas.addEventListener('webglcontextlost',onLost)
    frame = requestAnimationFrame(draw)
    return () => {
      disposed = true; cancelAnimationFrame(frame); observer.disconnect()
      canvas.removeEventListener('webglcontextlost',onLost)
      if (buffer) gl.deleteBuffer(buffer)
      shaders.forEach(shader => gl.deleteShader(shader))
      if (program) gl.deleteProgram(program)
    }
  }, [])

  return <div className={`orb-renderer ${fallback ? 'renderer-fallback' : ''}`} data-state={state} onPointerMove={event => {
    if (rotation.current.locked) return
    const bounds = event.currentTarget.getBoundingClientRect()
    rotation.current.x = (event.clientX-bounds.left)/bounds.width*2-1
    rotation.current.y = (event.clientY-bounds.top)/bounds.height*2-1
  }} onPointerLeave={() => { if (!rotation.current.locked) rotation.current = { x:0,y:0,locked:false } }}>
    <canvas ref={canvasRef} className="orb-canvas" aria-label={`3D AI orb: ${state}`} role="img" aria-describedby={fallback ? 'orb-rendering-unavailable' : undefined} />
    {fallback && <div className="orb-fallback" role="status"><span className="fallback-sphere" /><p id="orb-rendering-unavailable">3D rendering unavailable<br /><small>Assistant features remain available.</small></p></div>}
    <div className="orb-view-controls" aria-label="Orb view controls">
      <button type="button" aria-label="Rotate orb left" onClick={() => { rotation.current.x -= .4; rotation.current.locked=true }}><Icon name="rotate" /></button>
      <button type="button" aria-label="Reset orb view" onClick={() => { rotation.current={x:0,y:0,locked:false} }}><Icon name="reset" /></button>
      <button type="button" aria-label={paused?'Resume orb animation':'Pause orb animation'} aria-pressed={paused} onClick={() => setPaused(value => !value)}><Icon name={paused?'play':'pause'} /></button>
    </div>
  </div>
}
