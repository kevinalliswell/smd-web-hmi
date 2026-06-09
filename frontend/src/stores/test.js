import { defineStore } from 'pinia'
import { ref } from 'vue'

const MAX_SAMPLES = 600 // 滚动保留最近样本（约 10 min @1s）

export const useTestStore = defineStore('test', () => {
  const currentTest = ref(null) // {test_id, start_time, operator_id, state}
  const recentSamples = ref([])

  function setCurrentTest(test) {
    currentTest.value = test
  }

  function appendSample(sample) {
    recentSamples.value.push(sample)
    if (recentSamples.value.length > MAX_SAMPLES) {
      recentSamples.value.splice(0, recentSamples.value.length - MAX_SAMPLES)
    }
  }

  function endTest() {
    currentTest.value = null
    recentSamples.value = []
  }

  return { currentTest, recentSamples, setCurrentTest, appendSample, endTest }
})
