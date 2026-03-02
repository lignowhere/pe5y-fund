export type CreateWarehouseDTO = {
  name: string;
  locationCode: string;
  address?: string;
  country?: string;
};

export type UpdateWarehouseDTO = Partial<CreateWarehouseDTO>;
