/**
 * API client for Payment Lab backend.
 * All endpoints are proxied through Vite dev server to Flask at :5000.
 */

const API_BASE = '/api';

// Admin auth for the tool registry (propose/approve/reject). Not a real
// session system -- just a shared secret the dashboard sends as a bearer
// token, matched against PAYMENTLAB_ADMIN_KEY on the server. Stored in
// localStorage so it survives a page reload.
const ADMIN_KEY_STORAGE_KEY = 'paymentlab_admin_key';

export function getAdminKey() {
  return localStorage.getItem(ADMIN_KEY_STORAGE_KEY) || '';
}

export function setAdminKey(key) {
  if (key) {
    localStorage.setItem(ADMIN_KEY_STORAGE_KEY, key);
  } else {
    localStorage.removeItem(ADMIN_KEY_STORAGE_KEY);
  }
}

export function adminAuthHeaders() {
  const key = getAdminKey();
  return key ? { Authorization: `Bearer ${key}` } : {};
}

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
