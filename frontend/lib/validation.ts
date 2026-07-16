/**
 * Zod 请求校验 + 响应类型安全工具
 *
 * 提供统一的 API 请求/响应校验方案：
 * 1. validateRequest<T> — 校验请求体，返回类型安全的数据
 * 2. validateResponse<T> — 校验 API 响应，防止后端返回意外结构
 * 3. createApiCall — 封装带校验的 API 调用
 */

import { z, ZodSchema, ZodError } from 'zod';
import api from '@/lib/api';

/** 校验错误格式 */
export interface ValidationError {
  field: string;
  message: string;
}

/** 将 ZodError 转为扁平化的字段错误列表 */
export function flattenZodError(error: ZodError): ValidationError[] {
  return error.issues.map((issue) => ({
    field: issue.path.join('.'),
    message: issue.message,
  }));
}

/** 校验请求体 */
export function validateRequest<T>(
  schema: ZodSchema<T>,
  data: unknown
): { success: true; data: T } | { success: false; errors: ValidationError[] } {
  const result = schema.safeParse(data);
  if (result.success) {
    return { success: true, data: result.data };
  }
  return { success: false, errors: flattenZodError(result.error) };
}

/** 校验 API 响应 */
export function validateResponse<T>(
  schema: ZodSchema<T>,
  data: unknown
): { success: true; data: T } | { success: false; errors: ValidationError[] } {
  const result = schema.safeParse(data);
  if (result.success) {
    return { success: true, data: result.data };
  }
  // 生产环境只 warn，不阻断
  console.warn('[API Response Validation Warning]', flattenZodError(result.error));
  return { success: false, errors: flattenZodError(result.error) };
}

/** 创建带校验的 API 调用 */
export function createApiCall<TReq, TRes>(
  method: 'get' | 'post' | 'put' | 'patch' | 'delete',
  path: string,
  reqSchema: ZodSchema<TReq>,
  resSchema: ZodSchema<TRes>
) {
  return async (data: TReq): Promise<TRes> => {
    // 1. 校验请求
    const reqResult = validateRequest(reqSchema, data);
    if (!reqResult.success) {
      throw new ApiValidationError('Request validation failed', reqResult.errors);
    }

    // 2. 发送请求
    const response = method === 'get'
      ? await api.get(path, { params: reqResult.data })
      : await api.post(path, reqResult.data);

    // 3. 校验响应
    const resResult = validateResponse(resSchema, response.data);
    if (!resResult.success) {
      // 响应校验失败时仍返回原始数据，但记录警告
      console.warn('[API Response Schema Mismatch]', path, resResult.errors);
      return response.data as TRes;
    }

    return resResult.data;
  };
}

/** API 校验错误 */
export class ApiValidationError extends Error {
  errors: ValidationError[];

  constructor(message: string, errors: ValidationError[]) {
    super(message);
    this.name = 'ApiValidationError';
    this.errors = errors;
  }
}

// ---- 常用 Schema ----

export const PaginationSchema = z.object({
  offset: z.number().int().min(0).default(0),
  limit: z.number().int().min(1).max(100).default(20),
});

export const IdSchema = z.string().uuid();

export const TimestampSchema = z.string().datetime();

export const BaseReadSchema = z.object({
  id: IdSchema,
  created_at: TimestampSchema,
  updated_at: TimestampSchema,
});
