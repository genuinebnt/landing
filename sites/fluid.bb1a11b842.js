/*
 * Pointer-driven fluid, behind the grid. (v5 - water under a hand)
 *
 * A GPU Navier-Stokes solver: the pointer injects velocity and dye, and each
 * frame advects them, computes curl and adds vorticity confinement (which is
 * what keeps the swirls from smearing away), then projects the velocity field
 * divergence-free with a Jacobi pressure solve.
 *
 * The method and shader structure follow Pavel Dobryakov's WebGL-Fluid-
 * Simulation (MIT), which is also what Inspira UI's fluid-cursor wraps.
 * Condensed here, and tuned quiet: the reference splats on every mouse move in
 * rainbow. This one moves the fluid gently as you pass and only blooms on
 * click, in the page's own accents.
 */
(function () {
  var host = document.querySelector('.gridpan');
  if (!host || !host.parentNode) return;
  if (matchMedia('(hover: none)').matches) return;      // no pointer, no point

  var canvas = document.createElement('canvas');
  canvas.className = 'mfluid-gl';
  canvas.setAttribute('aria-hidden', 'true');
  host.parentNode.insertBefore(canvas, host);           // under the grid

  var gl = canvas.getContext('webgl2', { alpha: true, premultipliedAlpha: false, antialias: false });
  var is2 = !!gl;
  if (!gl) gl = canvas.getContext('webgl', { alpha: true, premultipliedAlpha: false, antialias: false });
  if (!gl) return;

  var halfFloat, supportLinear;
  if (is2) {
    gl.getExtension('EXT_color_buffer_float');
    supportLinear = !!gl.getExtension('OES_texture_float_linear');
  } else {
    halfFloat = gl.getExtension('OES_texture_half_float');
    supportLinear = !!gl.getExtension('OES_texture_half_float_linear');
    if (!halfFloat) return;
  }
  var HALF = is2 ? gl.HALF_FLOAT : halfFloat.HALF_FLOAT_OES;

  function fmt(internal, format, type) {
    // some drivers refuse RG/R render targets — fall back to RGBA
    var tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, gl.NEAREST);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, internal, 4, 4, 0, format, type, null);
    var fbo = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fbo);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    var ok = gl.checkFramebufferStatus(gl.FRAMEBUFFER) === gl.FRAMEBUFFER_COMPLETE;
    gl.deleteFramebuffer(fbo); gl.deleteTexture(tex);
    return ok ? { internal: internal, format: format } : null;
  }
  var RGBA = is2 ? fmt(gl.RGBA16F, gl.RGBA, HALF) : { internal: gl.RGBA, format: gl.RGBA };
  var RG   = is2 ? (fmt(gl.RG16F, gl.RG, HALF) || RGBA) : RGBA;
  var R    = is2 ? (fmt(gl.R16F, gl.RED, HALF) || RGBA) : RGBA;
  if (!RGBA) return;

  function compile(type, src) {
    var s = gl.createShader(type);
    gl.shaderSource(s, (is2 ? '#version 300 es\n' : '') + src);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS)) { console.warn(gl.getShaderInfoLog(s)); return null; }
    return s;
  }
  // one shader source, two dialects
  var PRE = is2
    ? '#define VARY out\n#define VARY_IN in\n#define TEX texture\n#define FRAG out highp vec4 fragColor;\n#define OUT fragColor\n'
    : '#define VARY varying\n#define VARY_IN varying\n#define TEX texture2D\n#define FRAG\n#define OUT gl_FragColor\n';

  var VERT = PRE + [
    'precision highp float;',
    is2 ? 'in vec2 aPosition;' : 'attribute vec2 aPosition;',
    'VARY vec2 vUv; VARY vec2 vL; VARY vec2 vR; VARY vec2 vT; VARY vec2 vB;',
    'uniform vec2 texelSize;',
    'void main () {',
    '  vUv = aPosition * 0.5 + 0.5;',
    '  vL = vUv - vec2(texelSize.x, 0.0); vR = vUv + vec2(texelSize.x, 0.0);',
    '  vT = vUv + vec2(0.0, texelSize.y); vB = vUv - vec2(0.0, texelSize.y);',
    '  gl_Position = vec4(aPosition, 0.0, 1.0);',
    '}'].join('\n');

  function frag(body) { return PRE + 'precision highp float; precision highp sampler2D;\nFRAG\n' + body; }

  var SH = {
    copy: frag('VARY_IN vec2 vUv; uniform sampler2D uTexture; void main(){ OUT = TEX(uTexture, vUv); }'),
    clear: frag('VARY_IN vec2 vUv; uniform sampler2D uTexture; uniform float value; void main(){ OUT = value * TEX(uTexture, vUv); }'),
    display: frag([
      'VARY_IN vec2 vUv; uniform sampler2D uTexture;',
      'void main(){ vec3 c = TEX(uTexture, vUv).rgb;',
      '  float a = clamp(pow(max(c.r, max(c.g, c.b)) * 1.7, 0.72), 0.0, 1.0);',
      '  OUT = vec4(c, a); }'].join('\n')),
    splat: frag([
      'VARY_IN vec2 vUv; uniform sampler2D uTarget; uniform float aspectRatio;',
      'uniform vec3 color; uniform vec2 point; uniform float radius;',
      'void main(){ vec2 p = vUv - point.xy; p.x *= aspectRatio;',
      '  vec3 splat = exp(-dot(p, p) / radius) * color;',
      '  OUT = vec4(TEX(uTarget, vUv).xyz + splat, 1.0); }'].join('\n')),
    advection: frag([
      'VARY_IN vec2 vUv; uniform sampler2D uVelocity; uniform sampler2D uSource;',
      'uniform vec2 texelSize; uniform float dt; uniform float dissipation;',
      'void main(){ vec2 coord = vUv - dt * TEX(uVelocity, vUv).xy * texelSize;',
      '  OUT = TEX(uSource, coord) / (1.0 + dissipation * dt); }'].join('\n')),
    divergence: frag([
      'VARY_IN vec2 vUv; VARY_IN vec2 vL; VARY_IN vec2 vR; VARY_IN vec2 vT; VARY_IN vec2 vB;',
      'uniform sampler2D uVelocity;',
      'void main(){ float L = TEX(uVelocity, vL).x, Rr = TEX(uVelocity, vR).x;',
      '  float T = TEX(uVelocity, vT).y, B = TEX(uVelocity, vB).y;',
      '  vec2 C = TEX(uVelocity, vUv).xy;',
      '  if (vL.x < 0.0) L = -C.x; if (vR.x > 1.0) Rr = -C.x;',
      '  if (vT.y > 1.0) T = -C.y; if (vB.y < 0.0) B = -C.y;',
      '  OUT = vec4(0.5 * (Rr - L + T - B), 0.0, 0.0, 1.0); }'].join('\n')),
    curl: frag([
      'VARY_IN vec2 vL; VARY_IN vec2 vR; VARY_IN vec2 vT; VARY_IN vec2 vB;',
      'uniform sampler2D uVelocity;',
      'void main(){ float L = TEX(uVelocity, vL).y, Rr = TEX(uVelocity, vR).y;',
      '  float T = TEX(uVelocity, vT).x, B = TEX(uVelocity, vB).x;',
      '  OUT = vec4(0.5 * (Rr - L - T + B), 0.0, 0.0, 1.0); }'].join('\n')),
    vorticity: frag([
      'VARY_IN vec2 vUv; VARY_IN vec2 vL; VARY_IN vec2 vR; VARY_IN vec2 vT; VARY_IN vec2 vB;',
      'uniform sampler2D uVelocity; uniform sampler2D uCurl; uniform float curl; uniform float dt;',
      'void main(){ float L = TEX(uCurl, vL).x, Rr = TEX(uCurl, vR).x;',
      '  float T = TEX(uCurl, vT).x, B = TEX(uCurl, vB).x, C = TEX(uCurl, vUv).x;',
      '  vec2 force = 0.5 * vec2(abs(T) - abs(B), abs(Rr) - abs(L));',
      '  force /= length(force) + 0.0001; force *= curl * C; force.y *= -1.0;',
      '  vec2 vel = TEX(uVelocity, vUv).xy + force * dt;',
      '  vel = min(max(vel, -1000.0), 1000.0);',
      '  OUT = vec4(vel, 0.0, 1.0); }'].join('\n')),
    pressure: frag([
      'VARY_IN vec2 vUv; VARY_IN vec2 vL; VARY_IN vec2 vR; VARY_IN vec2 vT; VARY_IN vec2 vB;',
      'uniform sampler2D uPressure; uniform sampler2D uDivergence;',
      'void main(){ float L = TEX(uPressure, vL).x, Rr = TEX(uPressure, vR).x;',
      '  float T = TEX(uPressure, vT).x, B = TEX(uPressure, vB).x;',
      '  float div = TEX(uDivergence, vUv).x;',
      '  OUT = vec4((L + Rr + B + T - div) * 0.25, 0.0, 0.0, 1.0); }'].join('\n')),
    gradient: frag([
      'VARY_IN vec2 vUv; VARY_IN vec2 vL; VARY_IN vec2 vR; VARY_IN vec2 vT; VARY_IN vec2 vB;',
      'uniform sampler2D uPressure; uniform sampler2D uVelocity;',
      'void main(){ float L = TEX(uPressure, vL).x, Rr = TEX(uPressure, vR).x;',
      '  float T = TEX(uPressure, vT).x, B = TEX(uPressure, vB).x;',
      '  vec2 vel = TEX(uVelocity, vUv).xy - vec2(Rr - L, T - B);',
      '  OUT = vec4(vel, 0.0, 1.0); }'].join('\n'))
  };

  var vs = compile(gl.VERTEX_SHADER, VERT);
  function program(fsrc) {
    var p = gl.createProgram();
    gl.attachShader(p, vs); gl.attachShader(p, compile(gl.FRAGMENT_SHADER, fsrc));
    gl.linkProgram(p);
    if (!gl.getProgramParameter(p, gl.LINK_STATUS)) { console.warn(gl.getProgramInfoLog(p)); return null; }
    var u = {}, n = gl.getProgramParameter(p, gl.ACTIVE_UNIFORMS);
    for (var i = 0; i < n; i++) { var nm = gl.getActiveUniform(p, i).name; u[nm] = gl.getUniformLocation(p, nm); }
    return { p: p, u: u };
  }
  var P = {}; for (var k in SH) { P[k] = program(SH[k]); if (!P[k]) return; }

  var buf = gl.createBuffer();
  gl.bindBuffer(gl.ARRAY_BUFFER, buf);
  gl.bufferData(gl.ARRAY_BUFFER, new Float32Array([-1,-1, -1,1, 1,1, 1,-1]), gl.STATIC_DRAW);
  var ibuf = gl.createBuffer();
  gl.bindBuffer(gl.ELEMENT_ARRAY_BUFFER, ibuf);
  gl.bufferData(gl.ELEMENT_ARRAY_BUFFER, new Uint16Array([0,1,2, 0,2,3]), gl.STATIC_DRAW);
  gl.vertexAttribPointer(0, 2, gl.FLOAT, false, 0, 0);
  gl.enableVertexAttribArray(0);
  function blit(target) {
    gl.bindFramebuffer(gl.FRAMEBUFFER, target ? target.fbo : null);
    if (target) gl.viewport(0, 0, target.w, target.h);
    else gl.viewport(0, 0, gl.drawingBufferWidth, gl.drawingBufferHeight);
    gl.drawElements(gl.TRIANGLES, 6, gl.UNSIGNED_SHORT, 0);
  }

  function fbo(w, h, f, filter) {
    gl.activeTexture(gl.TEXTURE0);
    var tex = gl.createTexture();
    gl.bindTexture(gl.TEXTURE_2D, tex);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MIN_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_MAG_FILTER, filter);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_S, gl.CLAMP_TO_EDGE);
    gl.texParameteri(gl.TEXTURE_2D, gl.TEXTURE_WRAP_T, gl.CLAMP_TO_EDGE);
    gl.texImage2D(gl.TEXTURE_2D, 0, f.internal, w, h, 0, f.format, HALF, null);
    var fb = gl.createFramebuffer();
    gl.bindFramebuffer(gl.FRAMEBUFFER, fb);
    gl.framebufferTexture2D(gl.FRAMEBUFFER, gl.COLOR_ATTACHMENT0, gl.TEXTURE_2D, tex, 0);
    gl.viewport(0, 0, w, h); gl.clear(gl.COLOR_BUFFER_BIT);
    return { tex: tex, fbo: fb, w: w, h: h, texelX: 1/w, texelY: 1/h,
      attach: function (id) { gl.activeTexture(gl.TEXTURE0 + id); gl.bindTexture(gl.TEXTURE_2D, tex); return id; } };
  }
  function dbl(w, h, f, filter) {
    var a = fbo(w,h,f,filter), b = fbo(w,h,f,filter);
    return { w:w, h:h, texelX:1/w, texelY:1/h,
      get read(){return a;}, set read(v){a=v;}, get write(){return b;}, set write(v){b=v;},
      swap: function(){ var t=a; a=b; b=t; } };
  }

  var SIM = 168, DYE = 800, filter = supportLinear ? gl.LINEAR : gl.NEAREST;
  var dye, velocity, divergence, curlFbo, pressure;
  function init() {
    var sw = SIM, sh = Math.round(SIM * (canvas.height / canvas.width)) || SIM;
    var dw = DYE, dh = Math.round(DYE * (canvas.height / canvas.width)) || DYE;
    dye = dbl(dw, dh, RGBA, filter);
    velocity = dbl(sw, sh, RG, filter);
    divergence = fbo(sw, sh, R, gl.NEAREST);
    curlFbo = fbo(sw, sh, R, gl.NEAREST);
    pressure = dbl(sw, sh, R, gl.NEAREST);
  }
  function resize() {
    var d = Math.min(devicePixelRatio || 1, 1.5);
    var w = Math.floor(host.parentNode.clientWidth * d), h = Math.floor(innerHeight * d);
    if (canvas.width !== w || canvas.height !== h) { canvas.width = w; canvas.height = h; init(); }
  }
  resize();
  addEventListener('resize', resize, { passive: true });

  // ── the accents, as dye ────────────────────────────────────────────────
  var ACCENTS = (canvas.parentNode.getAttribute('data-fluid') || '224,152,90')
    .split('|').map(function (s) { return s.split(',').map(Number); });

  // On the pages whose chrome rekeys amber -> violet -> teal, the dye follows
  // the same 15s cycle rather than picking its own colour per splat, so the
  // fluid is always the accent the rest of the page is currently wearing.
  // These stops mirror the rekey keyframes exactly.
  var STOPS = [[0,0],[0.26,0],[0.33,1],[0.59,1],[0.66,2],[0.92,2],[1,0]];
  function accentNow() {
    if (ACCENTS.length === 1) return ACCENTS[0];
    var t = (performance.now() % 15000) / 15000;
    for (var i = 1; i < STOPS.length; i++) {
      if (t <= STOPS[i][0]) {
        var a = ACCENTS[STOPS[i-1][1]], b = ACCENTS[STOPS[i][1]];
        if (a === b) return a;
        var span = STOPS[i][0] - STOPS[i-1][0];
        var k = span > 0 ? (t - STOPS[i-1][0]) / span : 0;
        return [a[0]+(b[0]-a[0])*k, a[1]+(b[1]-a[1])*k, a[2]+(b[2]-a[2])*k];
      }
    }
    return ACCENTS[0];
  }
  function nextColor(gain) {
    var c = accentNow();
    return { r: c[0]/255*gain, g: c[1]/255*gain, b: c[2]/255*gain };
  }

  function use(prog) { gl.useProgram(prog.p); }
  function splat(x, y, dx, dy, color, radius) {
    use(P.splat);
    gl.uniform1i(P.splat.u.uTarget, velocity.read.attach(0));
    gl.uniform1f(P.splat.u.aspectRatio, canvas.width / canvas.height);
    gl.uniform2f(P.splat.u.point, x, y);
    gl.uniform3f(P.splat.u.color, dx, dy, 0);
    gl.uniform1f(P.splat.u.radius, radius / 100);
    blit(velocity.write); velocity.swap();

    gl.uniform1i(P.splat.u.uTarget, dye.read.attach(0));
    gl.uniform3f(P.splat.u.color, color.r, color.g, color.b);
    blit(dye.write); dye.swap();
  }

  var last = performance.now(), pointer = null, tick = 0;

  // Left alone, dye dissipates and momentum damps until the canvas is empty and
  // stays that way. A slow wandering source keeps the field moving for as long
  // as the page is open — weak enough to read as a current under the page
  // rather than as something being drawn, and the pointer still dominates it.
  function ambient(now) {
    var t = now * 0.000085;
    var x = 0.5 + Math.cos(t * 1.3) * 0.30;
    var y = 0.5 + Math.sin(t * 0.87) * 0.26;
    var a = t * 2.1;
    splat(x, y, Math.cos(a) * 90, Math.sin(a) * 90, nextColor(0.11), 0.8);
  }

  function step(now) {
    // The reference runs at real time, which reads as a splash. Stepping the
    // simulation at a third of it makes the same solver behave like something
    // viscous — the swirls take seconds to unwind instead of a frame or two.
    var dt = Math.min((now - last) / 1000, 0.016) * 0.34; last = now;
    resize();
    if ((tick++ % 22) === 0) ambient(now);
    gl.disable(gl.BLEND);

    use(P.curl);
    gl.uniform2f(P.curl.u.texelSize, velocity.texelX, velocity.texelY);
    gl.uniform1i(P.curl.u.uVelocity, velocity.read.attach(0));
    blit(curlFbo);

    use(P.vorticity);
    gl.uniform2f(P.vorticity.u.texelSize, velocity.texelX, velocity.texelY);
    gl.uniform1i(P.vorticity.u.uVelocity, velocity.read.attach(0));
    gl.uniform1i(P.vorticity.u.uCurl, curlFbo.attach(1));
    gl.uniform1f(P.vorticity.u.curl, 18);   // low: a hand on water slides a sheet rather than spinning eddies
    gl.uniform1f(P.vorticity.u.dt, dt);
    blit(velocity.write); velocity.swap();

    use(P.divergence);
    gl.uniform2f(P.divergence.u.texelSize, velocity.texelX, velocity.texelY);
    gl.uniform1i(P.divergence.u.uVelocity, velocity.read.attach(0));
    blit(divergence);

    use(P.clear);
    gl.uniform1i(P.clear.u.uTexture, pressure.read.attach(0));
    gl.uniform1f(P.clear.u.value, 0.8);
    blit(pressure.write); pressure.swap();

    use(P.pressure);
    gl.uniform2f(P.pressure.u.texelSize, velocity.texelX, velocity.texelY);
    gl.uniform1i(P.pressure.u.uDivergence, divergence.attach(0));
    for (var i = 0; i < 26; i++) {
      gl.uniform1i(P.pressure.u.uPressure, pressure.read.attach(1));
      blit(pressure.write); pressure.swap();
    }

    use(P.gradient);
    gl.uniform2f(P.gradient.u.texelSize, velocity.texelX, velocity.texelY);
    gl.uniform1i(P.gradient.u.uPressure, pressure.read.attach(0));
    gl.uniform1i(P.gradient.u.uVelocity, velocity.read.attach(1));
    blit(velocity.write); velocity.swap();

    use(P.advection);
    gl.uniform2f(P.advection.u.texelSize, velocity.texelX, velocity.texelY);
    gl.uniform1i(P.advection.u.uVelocity, velocity.read.attach(0));
    gl.uniform1i(P.advection.u.uSource, velocity.read.attach(0));
    gl.uniform1f(P.advection.u.dt, dt);
    gl.uniform1f(P.advection.u.dissipation, 0.055);  // gently damped, so the push glides instead of churning
    blit(velocity.write); velocity.swap();

    gl.uniform1i(P.advection.u.uVelocity, velocity.read.attach(0));
    gl.uniform1i(P.advection.u.uSource, dye.read.attach(1));
    gl.uniform1f(P.advection.u.dissipation, 0.16);   // keeps its body, but reaches a steady state
    blit(dye.write); dye.swap();

    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.enable(gl.BLEND);
    use(P.display);
    gl.uniform1i(P.display.u.uTexture, dye.read.attach(0));
    blit(null);

    requestAnimationFrame(step);
  }
  requestAnimationFrame(step);

  // ── input ──────────────────────────────────────────────────────────────
  function norm(e) {
    var r = canvas.getBoundingClientRect();
    return { x: (e.clientX - r.left) / r.width, y: 1 - (e.clientY - r.top) / r.height };
  }
  addEventListener('pointermove', function (e) {
    var p = norm(e);
    if (pointer) {
      var dx = (p.x - pointer.x) * 1500, dy = (p.y - pointer.y) * 1500;
      // Only a nudge on hover — the bloom is reserved for a click, so passing
      // the cursor over the page stirs it rather than painting on it.
      if (Math.abs(dx) + Math.abs(dy) > 1)
        splat(p.x, p.y, dx, dy, nextColor(0.13), 0.40);
    }
    pointer = p;
  }, { passive: true });

  addEventListener('pointerdown', function (e) {
    // Clicking a link or a card should do what it says, not bloom.
    if (e.target.closest && e.target.closest('a,button,input,textarea,select,summary')) return;
    var p = norm(e);
    splat(p.x, p.y, (Math.random() - 0.5) * 240, (Math.random() - 0.5) * 240, nextColor(0.8), 0.9);
    for (var i = 0; i < 4; i++) {
      var a = Math.random() * Math.PI * 2, d = 0.03 + Math.random() * 0.04;
      splat(p.x + Math.cos(a) * d, p.y + Math.sin(a) * d,
            Math.cos(a) * 320, Math.sin(a) * 320, nextColor(0.5), 0.6);
    }
  }, { passive: true });
})();
