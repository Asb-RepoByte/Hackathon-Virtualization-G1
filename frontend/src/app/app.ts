import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient } from '@angular/common/http';

interface Device {
  id: number;
  manufacturer: string;
  name: string;
  category: string;
  subcategory: string;
  gwp_total: number;
  gwp_use_ratio: number;
  gwp_manufacturing_ratio: number;
  lifetime: number;
}

interface Equivalences {
  car_km: number;
  smartphone_charges: number;
  flight_km: number;
  tree_years: number;
}

interface CalculationResult {
  device: Device;
  years_of_use: number;
  manufacturing_impact: number;
  use_impact: number;
  total_impact: number;
  equivalences: Equivalences;
}

@Component({
  selector: 'app-root',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './app.html',
  styleUrl: './app.css'
})
export class AppComponent implements OnInit {
  categories: string[] = [];
  manufacturers: string[] = [];
  devices: Device[] = [];

  selectedCategory = '';
  selectedManufacturer = '';
  selectedDeviceId: number | null = null;
  selectedDevice: Device | null = null;
  yearsOfUse: number = 3;

  result: CalculationResult | null = null;
  loading = false;
  error = '';

  private apiUrl = '/api';

  constructor(private http: HttpClient) { }

  ngOnInit(): void {
    this.loadCategories();
  }

  loadCategories(): void {
    this.http.get<string[]>(`${this.apiUrl}/categories`).subscribe({
      next: (data) => this.categories = data,
      error: () => this.error = 'Impossible de charger les catégories'
    });
  }

  onCategoryChange(): void {
    this.manufacturers = [];
    this.devices = [];
    this.selectedManufacturer = '';
    this.selectedDeviceId = null;
    this.selectedDevice = null;
    this.result = null;
    if (this.selectedCategory) {
      this.http.get<string[]>(`${this.apiUrl}/manufacturers?category=${encodeURIComponent(this.selectedCategory)}`).subscribe({
        next: (data) => this.manufacturers = data,
        error: () => this.error = 'Impossible de charger les fabricants'
      });
    }
  }

  onManufacturerChange(): void {
    this.devices = [];
    this.selectedDeviceId = null;
    this.selectedDevice = null;
    this.result = null;
    if (this.selectedManufacturer) {
      const params = new URLSearchParams({ manufacturer: this.selectedManufacturer });
      if (this.selectedCategory) params.append('category', this.selectedCategory);
      this.http.get<Device[]>(`${this.apiUrl}/devices?${params.toString()}`).subscribe({
        next: (data) => this.devices = data,
        error: () => this.error = 'Impossible de charger les appareils'
      });
    }
  }

  onDeviceChange(): void {
    this.selectedDevice = this.devices.find(d => d.id === this.selectedDeviceId) || null;
    this.result = null;
  }

  calculate(): void {
    if (!this.selectedDeviceId) return;
    this.loading = true;
    this.error = '';
    this.http.post<CalculationResult>(`${this.apiUrl}/calculate`, {
      device_id: this.selectedDeviceId,
      years_of_use: this.yearsOfUse
    }).subscribe({
      next: (data) => {
        this.result = data;
        this.loading = false;
      },
      error: () => {
        this.error = 'Erreur lors du calcul';
        this.loading = false;
      }
    });
  }

  reset(): void {
    this.result = null;
    this.selectedCategory = '';
    this.selectedManufacturer = '';
    this.selectedDeviceId = null;
    this.selectedDevice = null;
    this.manufacturers = [];
    this.devices = [];
    this.yearsOfUse = 3;
    this.error = '';
  }

  get manufacturingPercent(): number {
    if (!this.result || this.result.total_impact === 0) return 0;
    return (this.result.manufacturing_impact / this.result.total_impact) * 100;
  }

  get usePercent(): number {
    if (!this.result || this.result.total_impact === 0) return 0;
    return (this.result.use_impact / this.result.total_impact) * 100;
  }

  get impactLevel(): 'low' | 'medium' | 'high' {
    if (!this.result) return 'low';
    if (this.result.total_impact < 100) return 'low';
    if (this.result.total_impact < 500) return 'medium';
    return 'high';
  }
}
