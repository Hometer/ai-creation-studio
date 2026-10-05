import { apiErrorMessage } from './workflowTypes';

/** Legacy success envelopes remain compatible; failed/non-JSON responses are safe text. */
export async function readLegacyJsonResponse(response: Response) {
  let data;
  try { data = JSON.parse(await response.text()); }
  catch { throw new Error(`接口返回格式异常（HTTP ${response.status}）`); }
  if (!response.ok) throw new Error(apiErrorMessage(data, '请求失败'));
  if (!data || typeof data !== 'object' || Array.isArray(data)) throw new Error('接口返回格式异常');
  if (data.success === false) throw new Error(typeof data.error === 'string' ? data.error : apiErrorMessage(data, '操作失败，请重试'));
  return data;
}
