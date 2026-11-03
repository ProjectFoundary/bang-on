export const DETAIL_NOISE=`
precision highp sampler3D;
uniform sampler3D uDetailNoise;
// Volume rays and source masks branch on sparse density. Implicit derivatives
// are undefined there, so use an explicit level (0 unless supplied by the ray).
uniform float uDetailLod;
float detailBase(vec3 q){
  return textureLod(uDetailNoise,q/32.,max(0.,uDetailLod)).r;
}
vec3 detailWarp(vec3 q){
  return q+.65*(vec3(detailBase(q*.47+13.1),detailBase(q*.47+31.7),detailBase(q*.47+57.9))-.5);
}
float detailFbm(vec3 q){
  q=detailWarp(q);
  // Rotating the octave coordinates avoids aligned interpolation planes.
  mat3 r=mat3(.36,.48,-.8,-.8,.60,0.,.48,.64,.60);
  return .53*detailBase(q)+.27*detailBase(r*q*2.17+5.1)+.13*detailBase(r*r*q*4.91+11.7)+.07*detailBase(q*10.63+21.1);
}
// Explicit footprint in q-units, for a sampled optical density field. This
// remains defined in sparse/non-uniform shader branches and filters each octave.
float detailFilteredBase(vec3 q,float footprint){
  float lod=log2(max(1.,footprint*4.));
  return textureLod(uDetailNoise,q/32.,lod).r;
}
float detailFilteredFbm(vec3 q,float footprint){
  q+=.65*(vec3(detailFilteredBase(q*.47+13.1,footprint*.47),
    detailFilteredBase(q*.47+31.7,footprint*.47),detailFilteredBase(q*.47+57.9,footprint*.47))-.5);
  mat3 r=mat3(.36,.48,-.8,-.8,.60,0.,.48,.64,.60);
  return .53*detailFilteredBase(q,footprint)+.27*detailFilteredBase(r*q*2.17+5.1,footprint*2.17)
    +.13*detailFilteredBase(r*r*q*4.91+11.7,footprint*4.91)+.07*detailFilteredBase(q*10.63+21.1,footprint*10.63);
}
`;
