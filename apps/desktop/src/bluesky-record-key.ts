export function newRecordKey() {
  const alphabet = '234567abcdefghijklmnopqrstuvwxyz'
  let number = (BigInt(Date.now()) * 1000n << 10n) | BigInt(crypto.getRandomValues(new Uint16Array(1))[0] & 1023)
  let encoded = ''
  for (let index = 0; index < 13; index += 1) { encoded = alphabet[Number(number & 31n)] + encoded; number >>= 5n }
  return encoded
}
