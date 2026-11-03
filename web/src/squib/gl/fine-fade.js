// Heavy dust has its own optical lifetime, shared by forces, density and grains.
// The stable population classification matches the settling integrator.
export const DUST_FADE_GLSL=`
uniform vec4 uDustFade;uniform int uDustSeed;
float dustParticleWeight(int id){
 if(uDustFade.y<=uDustFade.x||uDustFade.z<=0.)return 1.;
 uint q=uint(id)*0x85ebca77u^uint(uDustSeed);q^=q>>16u;q*=0x7feb352du;q^=q>>15u;
 bool heavy=float(q)*(1./4294967296.)<uDustFade.z;
 return heavy?1.-smoothstep(uDustFade.x,uDustFade.y,uDustFade.w):1.;
}
`;
