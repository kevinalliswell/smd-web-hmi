import { describe, expect, it } from 'vitest'
import { canonicalJson, paramCrc } from '@/utils/crc32'

describe('crc32 / canonicalJson', () => {
  it('canonicalJson 递归排序键且紧凑', () => {
    expect(canonicalJson({ b: 1, a: 2 })).toBe('{"a":2,"b":1}')
    expect(canonicalJson({ x: { d: 1, c: 2 } })).toBe('{"x":{"c":2,"d":1}}')
  })

  it('整数值浮点折叠（5.0→5），与 JSON 传输一致', () => {
    expect(canonicalJson({ v: 5.0 })).toBe('{"v":5}')
    expect(canonicalJson({ v: 3.5 })).toBe('{"v":3.5}')
  })

  it('paramCrc 返回 8 位小写十六进制', () => {
    const crc = paramCrc({ a: 1, b: 2 })
    expect(crc).toMatch(/^[0-9a-f]{8}$/)
  })

  it('与后端已校验值一致（wire-accurate 固定向量）', () => {
    // 该向量已用后端 compute_param_crc 核对（见提交记录的 wire-accurate 校验）
    const v = {
      process: { gas_switch_temp_deg_c: 500, total_flow_l_min: 5.0 },
      mfc: { n2_reduce_l_min: 3.5, deviation_pct: 0.5 },
    }
    expect(paramCrc(v)).toBe('09c2359e')
  })

  it('键顺序不影响 CRC', () => {
    const a = paramCrc({ x: 1, y: 2 })
    const b = paramCrc({ y: 2, x: 1 })
    expect(a).toBe(b)
  })
})
