import apiClient from './client'
import { sendOperation } from './commands'
/** @typedef {import('./contracts').RecipeDefinition} RecipeDefinition */
/** @typedef {import('./contracts').RecipeVersion} RecipeVersion */
const path = (id) => `/api/recipes/${encodeURIComponent(id)}`
/** @returns {Promise<{items: RecipeVersion[], page: number, size: number, total: number}>} */
export const fetchRecipes = (page = 1) => apiClient.get('/api/recipes', { params: { page, size: 20 } }).then((r) => r.data.data)
/** @returns {Promise<{definition: RecipeDefinition}>} */
export const fetchStandardTemplate = () => apiClient.get('/api/recipes/template/standard').then((r) => r.data.data)
/** @returns {Promise<RecipeVersion[]>} */
export const fetchRecipeVersions = (id) => apiClient.get(`${path(id)}/versions`).then((r) => r.data.data.versions)
/** @param {RecipeDefinition} definition @returns {Promise<RecipeVersion>} */
export const saveRecipe = (definition, id = '') => (id ? apiClient.put(path(id), { definition }) : apiClient.post('/api/recipes', { definition })).then((r) => r.data.data)
export const validateRecipe = (id, version) => apiClient.post(`${path(id)}/validate`, { version }).then((r) => r.data.data)
/** @param {string} id @param {number} version */
export const activateRecipe = (id, version) => sendOperation('activate_recipe', { recipe_id: id, version }, (operationId) => apiClient.post(`${path(id)}/activate`, { version, operation_id: operationId }, { headers: { 'Idempotency-Key': operationId } }))
