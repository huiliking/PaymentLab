/**
 * API client for Payment Lab backend.
 * All endpoints are proxied through Vite dev server to Flask at :5000.
 */

const API_BASE = '/api';

export async function fetchConfig() {
  const res = await fetch(`${API_BASE}/config`);
  if (!res.ok) throw new Error('Failed to load config');
  return res.json();
}

export async function fetchProducts() {
  const res = await fetch(`${API_BASE}/products`);
  if (!res.ok) throw new Error('Failed to load products');
  return res.json();
}

export async function createPaymentIntent({ items, currency, customerEmail, locale, billingCountry, billingAddress }) {
  const res = await fetch(`${API_BASE}/create-payment-intent`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      items,
      currency,
      customer_email: customerEmail,
      locale,
      billing_country: billingCountry,
      billing_address: billingAddress,
    }),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || 'Payment failed');
  return data;
}

export async function checkPaymentStatus(paymentIntentId) {
  const res = await fetch(`${API_BASE}/payment-status/${paymentIntentId}`);
  if (!res.ok) throw new Error('Failed to check status');
  return res.json();
}
