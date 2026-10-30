  (function silk(){
    var cv=document.getElementById('silk');
    if(!cv) return;
    var gl=cv.getContext('webgl',{antialias:false,alpha:false,powerPreference:'low-power'})
         ||cv.getContext('experimental-webgl');
    if(!gl){document.body.classList.add('no-gl');return;}

    var VS='attribute vec2 p;void main(){gl_Position=vec4(p,0.,1.);}';
    var FS=[
      'precision highp float;',
      'uniform vec2 u_res;uniform float u_t;uniform float u_light;',
      'vec2 h2(vec2 p){p=vec2(dot(p,vec2(127.1,311.7)),dot(p,vec2(269.5,183.3)));',
      '  return -1.+2.*fract(sin(p)*43758.5453123);}',
      'float nz(vec2 p){const float K1=0.366025404;const float K2=0.211324865;',
      '  vec2 i=floor(p+(p.x+p.y)*K1);vec2 a=p-i+(i.x+i.y)*K2;',
      '  float m=step(a.y,a.x);vec2 o=vec2(m,1.-m);',
      '  vec2 b=a-o+K2;vec2 c=a-1.+2.*K2;',
      '  vec3 w=max(0.5-vec3(dot(a,a),dot(b,b),dot(c,c)),0.);',
      '  vec3 n=w*w*w*w*vec3(dot(a,h2(i)),dot(b,h2(i+o)),dot(c,h2(i+1.)));',
      '  return dot(n,vec3(70.));}',
      'float fbm(vec2 p){float v=0.;float a=0.55;mat2 r=mat2(0.8,0.6,-0.6,0.8);',
      '  for(int i=0;i<3;i++){v+=a*nz(p);p=r*p*2.0;a*=0.5;}return v;}',
      'void main(){',
      '  vec2 p=(gl_FragCoord.xy*2.-u_res)/min(u_res.x,u_res.y);',
      '  float ux=gl_FragCoord.x/u_res.x;',
      '  float t=u_t*0.045;',
      /* анизотропия вдоль складки + двойной domain warp = ткань, а не мрамор */
      '  float ca=cos(-0.6);float sa=sin(-0.6);',
      '  vec2 s=vec2((ca*p.x-sa*p.y)*0.25, sa*p.x+ca*p.y);',
      '  vec2 q=vec2(fbm(vec2(s.x*0.378,s.y*0.378+t*0.5)),',
      '              fbm(vec2(s.x*0.378+3.1,s.y*0.378+1.7-t*0.4)));',
      '  vec2 cloth=vec2(s.x*0.42+1.45*q.x,s.y*0.42+1.45*q.y);',
      '  float f=fbm(cloth);',
      '  float band=f*0.85+0.5;',
      /* палитра MS Product по X: бургунди → пыльная роза → винный, медленный дрейф */
      '  vec3 base=vec3(0.075,0.043,0.063);',
      '  vec3 vio=vec3(0.600,0.318,0.424);',
      '  vec3 mag=vec3(0.710,0.431,0.529);',
      '  vec3 cya=vec3(0.420,0.169,0.275);',
      '  float hx=clamp(ux+0.07*sin(u_t*0.008),0.,1.);',
      '  vec3 tint=hx<0.5?mix(mag,vio,hx/0.5):mix(vio,cya,(hx-0.5)/0.5);',
      '  vec3 col=mix(base,tint*0.58,smoothstep(0.05,0.78,band));',
      '  col=mix(col,tint*0.90,smoothstep(0.5,0.95,band)*0.45);',
      /* узкая световая жилка по гребню — не плоское пятно */
      '  float ridge=smoothstep(0.78,0.90,band)*(1.-smoothstep(0.90,1.02,band));',
      '  col+=vec3(1.0)*ridge*0.22;',
      '  vec3 lightBase=vec3(0.976,0.964,0.956);',
      '  vec3 lightFold=vec3(0.69,0.48,0.55);',
      '  float shade=smoothstep(0.12,0.88,band);',
      '  vec3 lightCol=mix(lightBase,lightFold,0.14+0.34*shade);',
      '  lightCol+=vec3(0.09,0.08,0.08)*ridge;',
      /* Мягкая виньетка без мелкой движущейся фактуры. */
      '  float rad=length(vec2(p.x*0.8,p.y));',
      '  col*=mix(0.40,1.0,1.-smoothstep(0.2,1.35,rad));',
      '  col=mix(col,lightCol,u_light);',
      '  gl_FragColor=vec4(col,1.0);}'
    ].join('\n');

    function sh(type,src){
      var s=gl.createShader(type);gl.shaderSource(s,src);gl.compileShader(s);
      return gl.getShaderParameter(s,gl.COMPILE_STATUS)?s:null;
    }
    var vs=sh(gl.VERTEX_SHADER,VS), fs=sh(gl.FRAGMENT_SHADER,FS);
    if(!vs||!fs){document.body.classList.add('no-gl');return;}
    var pr=gl.createProgram();
    gl.attachShader(pr,vs);gl.attachShader(pr,fs);gl.linkProgram(pr);
    if(!gl.getProgramParameter(pr,gl.LINK_STATUS)){document.body.classList.add('no-gl');return;}
    gl.useProgram(pr);

    var buf=gl.createBuffer();
    gl.bindBuffer(gl.ARRAY_BUFFER,buf);
    gl.bufferData(gl.ARRAY_BUFFER,new Float32Array([-1,-1,3,-1,-1,3]),gl.STATIC_DRAW);
    var loc=gl.getAttribLocation(pr,'p');
    gl.enableVertexAttribArray(loc);
    gl.vertexAttribPointer(loc,2,gl.FLOAT,false,0,0);
    var uRes=gl.getUniformLocation(pr,'u_res'), uT=gl.getUniformLocation(pr,'u_t');
    var uLight=gl.getUniformLocation(pr,'u_light');
    /* рендерим в пониженном разрешении — шёлк гладкий, апскейл незаметен */
    var SCALE=Math.min(window.devicePixelRatio||1,1.5)*0.9;
    function resize(){
      var w=Math.max(1,Math.round(cv.clientWidth*SCALE));
      var h=Math.max(1,Math.round(cv.clientHeight*SCALE));
      if(cv.width!==w||cv.height!==h){cv.width=w;cv.height=h;gl.viewport(0,0,w,h);}
      gl.uniform2f(uRes,cv.width,cv.height);
    }
    window.addEventListener('resize',resize);

    var motion=window.matchMedia('(prefers-reduced-motion:reduce)');
    var still=motion.matches;
    motion.addEventListener('change',function(e){still=e.matches;if(raf){cancelAnimationFrame(raf);raf=null;}last=null;raf=requestAnimationFrame(frame);});
    var acc=0, last=null, raf=null;
    var themeMix=document.documentElement.dataset.theme==='light'?1:0;
    function frame(now){
      var dt=last===null?0:Math.min((now-last)/1000,0.05);
      acc+=dt;
      last=now;
      resize();
      gl.uniform1f(uT,acc);
      var target=document.documentElement.dataset.theme==='light'?1:0;
      themeMix=still?target:themeMix+(target-themeMix)*(1-Math.exp(-dt/0.24));
      if(Math.abs(target-themeMix)<0.001) themeMix=target;
      gl.uniform1f(uLight,themeMix);
      gl.drawArrays(gl.TRIANGLES,0,3);
      cv.classList.add('ready');
      raf=still?null:requestAnimationFrame(frame);
    }
    raf=requestAnimationFrame(frame);
    new MutationObserver(function(){if(still && raf===null) raf=requestAnimationFrame(frame);}).observe(document.documentElement,{attributes:true,attributeFilter:['data-theme']});
    document.addEventListener('visibilitychange',function(){
      if(document.hidden){
        if(raf){cancelAnimationFrame(raf);raf=null;}
      }else if(!still&&raf===null){
        last=null;raf=requestAnimationFrame(frame);
      }
    });
  })();

