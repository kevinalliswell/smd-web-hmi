import js from '@eslint/js'
import pluginVue from 'eslint-plugin-vue'
import globals from 'globals'
import tseslint from 'typescript-eslint'

export default [
  {
    ignores: ['dist/**', 'coverage/**', 'src/api/generated/**'],
  },
  js.configs.recommended,
  ...pluginVue.configs['flat/essential'],
  {
    files: ['src/**/*.ts'],
    languageOptions: { parser: tseslint.parser },
    plugins: { '@typescript-eslint': tseslint.plugin },
    rules: { ...tseslint.configs.recommended[2].rules },
  },
  { files: ['src/**/*.d.ts'], rules: { 'no-unused-vars': 'off' } },
  {
    files: ['src/**/*.vue'],
    languageOptions: { parserOptions: { parser: tseslint.parser } },
  },
  {
    files: ['**/*.{js,ts,vue}'],
    languageOptions: {
      globals: {
        ...globals.browser,
        ...globals.node,
      },
    },
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
]
