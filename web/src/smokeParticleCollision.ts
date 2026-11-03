/** Opt-in finite-solid guards for Squib's authored live smoke particles.
 * The unmodified source programs remain the default for native Squib. */
function once(source:string,needle:string,replacement:string){
  if(!source.includes(needle))throw new Error(`Smoke particle shader source mismatch: ${needle.slice(0,72)}`);
  return source.replace(needle,replacement);
}

function transport(source:string,header:string){
  source=once(source,'// trilinear: ',header+'\n// trilinear: ');
  return once(source,
    'float solidV(ivec3 v){ return (v.y<0 || uBoxMin.y+(float(v.y)+0.5)*uVox < uGround) ? 1.0 : 0.0; }',
    '// Finite solidV is supplied by the volume adapter.');
}

export function smokeParticleShader(name:string,input:string,transportHeader:string,maskHeader:string):string{
  if(name==='smokeParticleDeposit'){
    let source=once(input,'flat out float vDensity;',
      'uniform vec3 uBoxMin;uniform float uVox;\n'+maskHeader+'\nflat out float vDensity;');
    source=once(source,
      'bool valid=m.x>0.&&all(greaterThanEqual(cell,ivec3(0)))&&all(lessThan(cell,ivec3(uGridDim)));',
      `bool valid=m.x>0.&&all(greaterThanEqual(cell,ivec3(0)))&&all(lessThan(cell,ivec3(uGridDim)));
       if(valid&&vfxSolidWorld(uBoxMin+(vec3(cell)+.5)*uVox)>.5)valid=false;`);
    return source;
  }
  if(name==='smokeParticleStep'){
    let source=transport(input,transportHeader);
    // Rejection sampling preserves the authored source distribution while
    // preventing source kernels from spawning on the far side of a solid.
    source=once(source,
      'vec3 candidate=s.xyz+d;if(!inBox(candidate))continue;',
      'vec3 candidate=s.xyz+d;if(!inBox(candidate)||solidV(ivec3(floor(candidate)))>.5||!volumeBirthVisible(candidate))continue;');
    source=once(source,
      'p=vec4(point,uAge);m=vec4(uBirthMass,uSourceData[source].yz,uBirthMass);',
      `p=vec4(point,uAge);m=vec4(uBirthMass,uSourceData[source].yz,uBirthMass);
       if(solidV(ivec3(floor(point)))>.5||!volumeBirthVisible(point)){oPosition=p;oMass=vec4(0.);return;}`);
    // Moving hard geometry can cover a cohort between model samples. Such
    // cohorts must not remain alive and later reappear beyond the obstacle.
    source=once(source,
      'if(m.x<=0.){oPosition=p;oMass=m;return;}',
      'if(solidV(ivec3(floor(p.xyz)))>.5)m.x=0.;if(m.x<=0.){oPosition=p;oMass=m;return;}');
    source=once(source,
      'vec3 a=S(uCarrier,p.xyz).xyz,mid=p.xyz+.5*uDt*a;',
      'vec3 a=S(uCarrier,p.xyz).xyz,mid=volumeTrace(p.xyz,p.xyz+.5*uDt*a);');
    source=once(source,
      'vec3 next=p.xyz+uDt*S(uCarrier,mid).xyz;',
      'vec3 next=volumeTrace(p.xyz,p.xyz+uDt*S(uCarrier,mid).xyz);');
    return source;
  }
  if(name==='smokeParticleNormalize'){
    let source=transport(input,transportHeader);
    source=once(source,
      'ivec3 v=vox();if(!valid(v)){o=vec4(0.);return;}',
      'ivec3 v=vox();if(!valid(v)||solidV(v)>.5){o=vec4(0.);return;}');
    return source;
  }
  throw new Error(`Unknown smoke particle shader: ${name}`);
}
