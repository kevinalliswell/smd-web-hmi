// 与后端 compute_param_crc 一致的参数 CRC：
//   canonical = JSON(排序键, 紧凑分隔符)  → 等价 Python json.dumps(sort_keys=True, separators=(',',':'))
//   crc = crc32(UTF-8(canonical))         → 等价 Python binascii.crc32（标准 IEEE CRC-32）
// 返回 8 位小写十六进制（不带 0x，后端会自动去 0x 前缀后比对）。

const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) {
      c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    }
    table[n] = c >>> 0
  }
  return table
})()

function crc32(str) {
  const bytes = new TextEncoder().encode(str)
  let crc = 0xffffffff
  for (let i = 0; i < bytes.length; i++) {
    crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8)
  }
  return (crc ^ 0xffffffff) >>> 0
}

// 递归排序键的稳定序列化（与 Python sort_keys + 紧凑分隔符一致）
export function canonicalJson(obj) {
  if (Array.isArray(obj)) return '[' + obj.map(canonicalJson).join(',') + ']'
  if (obj && typeof obj === 'object') {
    const keys = Object.keys(obj).sort()
    return '{' + keys.map((k) => JSON.stringify(k) + ':' + canonicalJson(obj[k])).join(',') + '}'
  }
  return JSON.stringify(obj)
}

export function paramCrc(values) {
  return crc32(canonicalJson(values)).toString(16).padStart(8, '0')
}
