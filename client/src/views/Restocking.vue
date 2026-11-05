<script setup>
import { ref, computed, watch, onMounted } from 'vue'
import { api } from '../api'
import { useI18n } from '../composables/useI18n'
import { formatCurrency, formatCurrencyWithDecimals } from '../utils/currency'

const { t, currentCurrency, translateProductName } = useI18n()

const STEP = 500

const forecasts = ref([])
const loading = ref(true)
const error = ref(null)
const budget = ref(0) // USD
const overrides = ref({}) // sku -> user-edited quantity
const submitting = ref(false)
const submitError = ref(null)
const placedOrder = ref(null)

// Slider max: cost of everything at forecasted demand, rounded up to a nice step
const maxBudget = computed(() => {
  const total = forecasts.value.reduce((s, f) => s + f.forecasted_demand * f.unit_cost, 0)
  return Math.max(STEP, Math.ceil(total / STEP) * STEP)
})

// Greedy fill by demand growth; first item that does not fit gets the leftover, then stop
const recommendation = computed(() => {
  const ranked = [...forecasts.value].sort(
    (a, b) => (b.forecasted_demand - b.current_demand) - (a.forecasted_demand - a.current_demand)
  )
  const rows = []
  let remaining = budget.value
  for (const f of ranked) {
    const fullCost = f.forecasted_demand * f.unit_cost
    if (remaining >= fullCost) {
      rows.push({ ...f, recommended: f.forecasted_demand })
      remaining -= fullCost
    } else {
      const units = Math.floor(remaining / f.unit_cost)
      if (units > 0) rows.push({ ...f, recommended: units })
      break
    }
  }
  return rows
})

const rows = computed(() =>
  recommendation.value.map(r => ({ ...r, quantity: overrides.value[r.item_sku] ?? r.recommended }))
)

const notFundedCount = computed(() => forecasts.value.length - rows.value.length)
const orderTotal = computed(() => rows.value.reduce((s, r) => s + r.quantity * r.unit_cost, 0))
const remainingBudget = computed(() => budget.value - orderTotal.value)
const leadTime = computed(() =>
  rows.value.reduce((m, r) => (r.quantity > 0 ? Math.max(m, r.lead_time_days) : m), 0)
)
const overBudget = computed(() => orderTotal.value > budget.value + 1e-9)
const canSubmit = computed(() => orderTotal.value > 0 && !overBudget.value && !submitting.value)

watch(budget, () => {
  overrides.value = {}
})

const setQuantity = (sku, event) => {
  const n = Math.floor(Number(event.target.value))
  overrides.value = { ...overrides.value, [sku]: Number.isFinite(n) && n > 0 ? n : 0 }
}

const loadData = async () => {
  try {
    loading.value = true
    error.value = null
    forecasts.value = await api.getDemandForecasts()
    budget.value = Math.round(maxBudget.value / 2 / STEP) * STEP
  } catch (err) {
    error.value = t('restocking.loadError', { message: err.message })
    console.error('Load error:', err)
  } finally {
    loading.value = false
  }
}

const placeOrder = async () => {
  if (!canSubmit.value) return
  try {
    submitting.value = true
    submitError.value = null
    placedOrder.value = null
    const items = rows.value
      .filter(r => r.quantity > 0)
      .map(r => ({ item_sku: r.item_sku, quantity: r.quantity }))
    placedOrder.value = await api.createRestockingOrder(items)
    overrides.value = {}
  } catch (err) {
    const message = err.response?.data?.detail || err.message
    submitError.value = t('restocking.error', { message: typeof message === 'string' ? message : err.message })
    console.error('Order error:', err)
  } finally {
    submitting.value = false
  }
}

onMounted(loadData)
</script>

<template>
  <div class="restocking">
    <div class="page-header">
      <h2>{{ t('restocking.title') }}</h2>
      <p>{{ t('restocking.description') }}</p>
    </div>

    <div v-if="loading" class="loading">{{ t('common.loading') }}</div>
    <div v-else-if="error" class="error">{{ error }}</div>
    <div v-else>
      <div v-if="placedOrder" class="success" role="status">
        {{ t('restocking.success', { orderNumber: placedOrder.order_number, days: placedOrder.lead_time_days }) }}
        <router-link to="/orders" class="success-link">{{ t('restocking.viewOrders') }}</router-link>
      </div>
      <div v-if="submitError" class="error" role="alert">{{ submitError }}</div>

      <div class="card budget-card">
        <div class="budget-header">
          <label for="budget-slider" class="budget-label">{{ t('restocking.budgetLabel') }}</label>
          <div class="budget-value">{{ formatCurrency(budget, currentCurrency) }}</div>
        </div>
        <input
          id="budget-slider"
          v-model.number="budget"
          type="range"
          class="budget-slider"
          min="0"
          :max="maxBudget"
          :step="STEP"
          :aria-valuetext="formatCurrency(budget, currentCurrency)"
        />
        <div class="budget-scale">
          <span>{{ formatCurrency(0, currentCurrency) }}</span>
          <span>{{ formatCurrency(maxBudget, currentCurrency) }}</span>
        </div>
      </div>

      <div class="card">
        <div class="card-header">
          <h3 class="card-title">{{ t('restocking.recommendedItems') }} ({{ rows.length }})</h3>
          <span v-if="rows.length && notFundedCount" class="muted">
            {{ t('restocking.notFunded', { count: notFundedCount }) }}
          </span>
        </div>

        <div v-if="rows.length === 0" class="empty-note">{{ t('restocking.empty') }}</div>
        <template v-else>
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>{{ t('restocking.table.sku') }}</th>
                  <th>{{ t('restocking.table.itemName') }}</th>
                  <th>{{ t('restocking.table.trend') }}</th>
                  <th class="num">{{ t('restocking.table.forecastedDemand') }}</th>
                  <th class="num">{{ t('restocking.table.quantity') }}</th>
                  <th class="num">{{ t('restocking.table.unitCost') }}</th>
                  <th class="num">{{ t('restocking.table.lineCost') }}</th>
                  <th class="num">{{ t('restocking.table.leadTime') }}</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="row in rows" :key="row.item_sku">
                  <td><strong>{{ row.item_sku }}</strong></td>
                  <td>{{ translateProductName(row.item_name) }}</td>
                  <td><span :class="['badge', row.trend]">{{ t(`trends.${row.trend}`) }}</span></td>
                  <td class="num">{{ row.forecasted_demand.toLocaleString() }}</td>
                  <td class="num">
                    <input
                      type="number"
                      min="0"
                      step="1"
                      class="qty-input"
                      :value="row.quantity"
                      :aria-label="`${t('restocking.table.quantity')}: ${row.item_sku}`"
                      @input="setQuantity(row.item_sku, $event)"
                    />
                    <span v-if="row.quantity < row.forecasted_demand" class="partial-label">
                      {{ t('restocking.partial') }}
                    </span>
                  </td>
                  <td class="num">{{ formatCurrencyWithDecimals(row.unit_cost, currentCurrency, 2) }}</td>
                  <td class="num">{{ formatCurrencyWithDecimals(row.quantity * row.unit_cost, currentCurrency, 2) }}</td>
                  <td class="num">{{ t('restocking.days', { count: row.lead_time_days }) }}</td>
                </tr>
              </tbody>
            </table>
          </div>

          <div class="summary">
            <div class="summary-item">
              <span class="summary-label">{{ t('restocking.orderTotal') }}</span>
              <span :class="['summary-value', { over: overBudget }]">{{ formatCurrencyWithDecimals(orderTotal, currentCurrency, 2) }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">{{ t('restocking.budget') }}</span>
              <span class="summary-value">{{ formatCurrency(budget, currentCurrency) }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">{{ t('restocking.remaining') }}</span>
              <span :class="['summary-value', { over: overBudget }]">{{ formatCurrencyWithDecimals(remainingBudget, currentCurrency, 2) }}</span>
            </div>
            <div class="summary-item">
              <span class="summary-label">{{ t('restocking.orderLeadTime') }}</span>
              <span class="summary-value">{{ t('restocking.days', { count: leadTime }) }}</span>
            </div>
            <div class="summary-action">
              <span v-if="overBudget" class="over-note">{{ t('restocking.overBudget') }}</span>
              <button class="po-button create" :disabled="!canSubmit" @click="placeOrder">
                {{ submitting ? t('restocking.placing') : t('restocking.placeOrder') }}
              </button>
            </div>
          </div>
        </template>
      </div>
    </div>
  </div>
</template>

<style scoped>
.success {
  background: #f0fdf4;
  border: 1px solid #bbf7d0;
  color: #166534;
  padding: 1rem;
  border-radius: 8px;
  margin: 0 0 1rem;
  font-size: 0.938rem;
}

.success-link {
  margin-left: 0.5rem;
  color: #166534;
  font-weight: 600;
  text-decoration: underline;
}

.budget-header {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  margin-bottom: 0.75rem;
}

.budget-label {
  font-size: 0.875rem;
  font-weight: 600;
  color: #64748b;
}

.budget-value {
  font-size: 1.875rem;
  font-weight: 700;
  color: #0f172a;
  letter-spacing: -0.025em;
}

.budget-slider {
  -webkit-appearance: none;
  appearance: none;
  width: 100%;
  height: 6px;
  border-radius: 3px;
  background: #e2e8f0;
  outline: none;
  cursor: pointer;
}

.budget-slider::-webkit-slider-thumb {
  -webkit-appearance: none;
  appearance: none;
  width: 20px;
  height: 20px;
  border-radius: 50%;
  background: #0f172a;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.4);
}

.budget-slider::-moz-range-thumb {
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #0f172a;
  border: 2px solid white;
  box-shadow: 0 1px 3px rgba(15, 23, 42, 0.4);
}

.budget-slider:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: 4px;
}

.budget-scale {
  display: flex;
  justify-content: space-between;
  margin-top: 0.5rem;
  font-size: 0.75rem;
  color: #64748b;
}

.muted {
  color: #64748b;
  font-size: 0.813rem;
}

.empty-note {
  padding: 1.5rem;
  text-align: center;
  color: #64748b;
  font-size: 0.938rem;
}

.num {
  text-align: right;
}

.qty-input {
  width: 80px;
  padding: 0.25rem 0.5rem;
  border: 1px solid #e2e8f0;
  border-radius: 6px;
  font-size: 0.875rem;
  color: #0f172a;
  text-align: right;
}

.qty-input:focus-visible {
  outline: 2px solid #3b82f6;
  outline-offset: 1px;
}

.partial-label {
  display: inline-block;
  margin-left: 0.5rem;
  padding: 0.125rem 0.5rem;
  border-radius: 6px;
  background: #fed7aa;
  color: #92400e;
  font-size: 0.688rem;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.025em;
}

.summary {
  display: flex;
  flex-wrap: wrap;
  align-items: flex-end;
  gap: 1.5rem;
  margin-top: 1rem;
  padding-top: 1rem;
  border-top: 1px solid #e2e8f0;
}

.summary-item {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
}

.summary-label {
  font-size: 0.75rem;
  font-weight: 600;
  color: #64748b;
  text-transform: uppercase;
  letter-spacing: 0.025em;
}

.summary-value {
  font-size: 1.125rem;
  font-weight: 700;
  color: #0f172a;
}

.summary-value.over {
  color: #991b1b;
}

.summary-action {
  margin-left: auto;
  display: flex;
  align-items: center;
  gap: 1rem;
}

.over-note {
  color: #991b1b;
  font-size: 0.813rem;
}

.po-button {
  padding: 0.5rem 1rem;
  border: none;
  border-radius: 6px;
  font-size: 0.813rem;
  font-weight: 600;
  cursor: pointer;
  transition: all 0.2s ease;
  white-space: nowrap;
}

.po-button.create {
  background: #3b82f6;
  color: white;
}

.po-button.create:hover:not(:disabled) {
  background: #2563eb;
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(59, 130, 246, 0.3);
}

.po-button:disabled {
  background: #cbd5e1;
  color: #64748b;
  cursor: not-allowed;
}
</style>
