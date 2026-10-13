<script setup lang="ts">
import { reactive } from 'vue'
import type { RefundRequest } from './types'

const emit = defineEmits<{ refund: [request: RefundRequest] }>()
const form = reactive<RefundRequest>({ orderId: '', userId: '', amount: 0 })

function submitRefund() {
  if (form.orderId.trim() && form.userId.trim() && form.amount > 0) {
    emit('refund', { ...form })
  }
}
</script>

<template>
  <form @submit.prevent="submitRefund">
    <label>订单编号 <input v-model="form.orderId" required /></label>
    <label>用户编号 <input v-model="form.userId" required /></label>
    <label>退款金额 <input v-model.number="form.amount" type="number" min="0.01" step="0.01" required /></label>
    <button type="submit">申请退款</button>
  </form>
</template>
