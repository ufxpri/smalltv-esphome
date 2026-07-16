#include "st7789v.h"
#include "esphome/core/log.h"

namespace esphome {
namespace st7789v {

static const char *const TAG = "st7789v";
static const size_t TEMP_BUFFER_SIZE = 128;
// Max pixels per streamed tile (1024 px = 2048 bytes; heap is tight). A 240x4
// strip = 960 px fits, so the PC pushes a full frame as 60 strip-tiles.
static const size_t STREAM_TILE_MAX_PIXELS = 1024;
static const uint32_t STREAM_IDLE_TIMEOUT_MS = 8000;   // no data this long -> local page
static const uint32_t STREAM_LOOP_BUDGET = 3072;       // max bytes/loop() to stay responsive

void ST7789V::setup() {
  ESP_LOGCONFIG(TAG, "Setting up SPI ST7789V...");
#ifdef USE_POWER_SUPPLY
  this->power_.request();
  // the PowerSupply component takes care of post turn-on delay
#endif
  this->spi_setup();
  this->dc_pin_->setup();  // OUTPUT

  this->init_reset_();

  this->write_command_(ST7789_SLPOUT);  // Sleep out
  delay(120);                           // NOLINT

  this->write_command_(ST7789_NORON);  // Normal display mode on

  // *** display and color format setting ***
  this->write_command_(ST7789_MADCTL);
  this->write_data_(ST7789_MADCTL_COLOR_ORDER);

  // JLX240 display datasheet
  this->write_command_(0xB6);
  this->write_data_(0x0A);
  this->write_data_(0x82);

  this->write_command_(ST7789_COLMOD);
  this->write_data_(0x55);
  delay(10);

  // *** ST7789V Frame rate setting ***
  this->write_command_(ST7789_PORCTRL);
  this->write_data_(0x0c);
  this->write_data_(0x0c);
  this->write_data_(0x00);
  this->write_data_(0x33);
  this->write_data_(0x33);

  this->write_command_(ST7789_GCTRL);  // Voltages: VGH / VGL
  this->write_data_(0x35);

  // *** ST7789V Power setting ***
  this->write_command_(ST7789_VCOMS);
  this->write_data_(0x28);  // JLX240 display datasheet

  this->write_command_(ST7789_LCMCTRL);
  this->write_data_(0x0C);

  this->write_command_(ST7789_VDVVRHEN);
  this->write_data_(0x01);
  this->write_data_(0xFF);

  this->write_command_(ST7789_VRHS);  // voltage VRHS
  this->write_data_(0x10);

  this->write_command_(ST7789_VDVS);
  this->write_data_(0x20);

  this->write_command_(ST7789_FRCTRL2);
  this->write_data_(0x0f);

  this->write_command_(ST7789_PWCTRL1);
  this->write_data_(0xa4);
  this->write_data_(0xa1);

  // *** ST7789V gamma setting ***
  this->write_command_(ST7789_PVGAMCTRL);
  this->write_data_(0xd0);
  this->write_data_(0x00);
  this->write_data_(0x02);
  this->write_data_(0x07);
  this->write_data_(0x0a);
  this->write_data_(0x28);
  this->write_data_(0x32);
  this->write_data_(0x44);
  this->write_data_(0x42);
  this->write_data_(0x06);
  this->write_data_(0x0e);
  this->write_data_(0x12);
  this->write_data_(0x14);
  this->write_data_(0x17);

  this->write_command_(ST7789_NVGAMCTRL);
  this->write_data_(0xd0);
  this->write_data_(0x00);
  this->write_data_(0x02);
  this->write_data_(0x07);
  this->write_data_(0x0a);
  this->write_data_(0x28);
  this->write_data_(0x31);
  this->write_data_(0x54);
  this->write_data_(0x47);
  this->write_data_(0x0e);
  this->write_data_(0x1c);
  this->write_data_(0x17);
  this->write_data_(0x1b);
  this->write_data_(0x1e);

  this->write_command_(ST7789_INVON);  // panel confirmed: needs inversion ON (test card = clean complement)

  // Clear display - ensures we do not see garbage at power-on
  this->draw_filled_rect_(0, 0, this->get_width_internal(), this->get_height_internal(), 0x0000);

  delay(120);  // NOLINT

  this->write_command_(ST7789_DISPON);  // Display on
  delay(120);                           // NOLINT

  backlight_(true);

  this->buffer_fragment_length_pixels_ =
      this->get_width_internal() * this->get_height_internal() / this->buffer_fragmentation_;
  this->init_internal_(this->get_buffer_length_());
  memset(this->buffer_, 0x00, this->get_buffer_length_());

  if (this->stream_port_ != 0) {
    this->stream_buf_ = new uint8_t[STREAM_TILE_MAX_PIXELS * 2];
    // RGB332 -> RGB565 expansion LUT (for adaptive 8-bit tiles)
    for (int i = 0; i < 256; i++) {
      uint8_t r3 = (i >> 5) & 0x07, g3 = (i >> 2) & 0x07, b2 = i & 0x03;
      uint8_t r5 = (r3 << 2) | (r3 >> 1), g6 = (g3 << 3) | g3, b5 = (b2 << 3) | (b2 << 1) | (b2 >> 1);
      this->lut332_[i] = ((uint16_t) r5 << 11) | ((uint16_t) g6 << 5) | b5;
    }
  }
}

void ST7789V::dump_config() {
  LOG_DISPLAY("", "SPI ST7789V", this);
  ESP_LOGCONFIG(TAG, "  Model: %s", this->model_str_);
  ESP_LOGCONFIG(TAG, "  Height: %u", this->height_);
  ESP_LOGCONFIG(TAG, "  Width: %u", this->width_);
  ESP_LOGCONFIG(TAG, "  Height Offset: %u", this->offset_height_);
  ESP_LOGCONFIG(TAG, "  Width Offset: %u", this->offset_width_);
  ESP_LOGCONFIG(TAG, "  Fragmentation: %u", this->buffer_fragmentation_);
  ESP_LOGCONFIG(TAG, "  8-bit color mode: %s", YESNO(this->eightbitcolor_));
  LOG_PIN("  CS Pin: ", this->cs_);
  LOG_PIN("  DC Pin: ", this->dc_pin_);
  LOG_PIN("  Reset Pin: ", this->reset_pin_);
  LOG_PIN("  B/L Pin: ", this->backlight_pin_);
  LOG_UPDATE_INTERVAL(this);
  ESP_LOGCONFIG(TAG, "  Data rate: %dMHz", (unsigned) (this->data_rate_ / 1000000));
#ifdef USE_POWER_SUPPLY
  ESP_LOGCONFIG(TAG, "  Power Supply Configured: yes");
#endif
}

float ST7789V::get_setup_priority() const { return setup_priority::PROCESSOR; }

void ST7789V::update() {
  // Hybrid mode: while the PC is streaming a rendered framebuffer, don't let a
  // local page overwrite it.
  if (this->streaming_active_())
    return;
  this->current_fragment_offset_pixels_ = 0;
  for (unsigned frag = 0; frag < this->buffer_fragmentation_; frag++) {
    this->clear_clipping_();
    this->start_clipping(
        0, this->current_fragment_offset_pixels_ / this->get_width_internal(), this->get_width_internal(),
        (this->current_fragment_offset_pixels_ + this->buffer_fragment_length_pixels_) / this->get_width_internal());

    this->do_update_();
    this->write_display_data();
    App.feed_wdt();
    this->current_fragment_offset_pixels_ += this->buffer_fragment_length_pixels_;
  }
}

bool ST7789V::streaming_active_() {
#ifdef USE_ESP8266
  return this->stream_port_ != 0 && this->stream_client_ && this->stream_client_.connected() &&
         (millis() - this->last_stream_ms_ < STREAM_IDLE_TIMEOUT_MS);
#else
  return false;
#endif
}

void ST7789V::loop() {
#ifdef USE_ESP8266
  if (this->stream_port_ == 0 || this->stream_buf_ == nullptr)
    return;

  // Lazily start the TCP server once WiFi is up.
  if (this->stream_server_ == nullptr) {
    if (WiFi.status() != WL_CONNECTED)
      return;
    this->stream_server_ = new WiFiServer(this->stream_port_);
    this->stream_server_->begin();
    this->stream_server_->setNoDelay(true);
    ESP_LOGI(TAG, "Framebuffer stream listening on port %u", this->stream_port_);
  }

  // Free a closed connection's lwip resources before accepting the next one —
  // otherwise the dead client's TCP buffers leak and starve the heap.
  if (this->stream_client_ && !this->stream_client_.connected())
    this->stream_client_.stop();

  // Accept a single client (the PC widget). Extra connections wait.
  if (!this->stream_client_ || !this->stream_client_.connected()) {
    WiFiClient c = this->stream_server_->available();
    if (c) {
      this->stream_client_ = c;
      this->stream_client_.setNoDelay(true);
      this->stream_client_.setTimeout(0);
      this->stream_hdr_pos_ = 0;
      this->tile_need_ = 0;
      this->tile_have_ = 0;
      this->last_stream_ms_ = millis();
      ESP_LOGI(TAG, "Framebuffer stream client connected");
    }
  }

  this->stream_service_();

  // Restore the local page the instant a stream ends.
  bool active = this->streaming_active_();
  if (this->was_streaming_ && !active)
    this->update();
  this->was_streaming_ = active;
#endif
}

void ST7789V::stream_service_() {
#ifdef USE_ESP8266
  if (!this->stream_client_ || !this->stream_client_.connected())
    return;

  uint32_t budget = STREAM_LOOP_BUDGET;
  while (budget > 0 && this->stream_client_.available() > 0) {
    if (this->tile_need_ == 0) {
      // Reading the 8-byte tile header, one byte at a time.
      int b = this->stream_client_.read();
      if (b < 0)
        break;
      budget--;
      this->last_stream_ms_ = millis();
      this->stream_hdr_[this->stream_hdr_pos_++] = (uint8_t) b;
      if (this->stream_hdr_pos_ == 8) {
        this->stream_hdr_pos_ = 0;
        this->tile_x_ = (this->stream_hdr_[0] << 8) | this->stream_hdr_[1];
        this->tile_y_ = (this->stream_hdr_[2] << 8) | this->stream_hdr_[3];
        uint16_t raw_w = (this->stream_hdr_[4] << 8) | this->stream_hdr_[5];
        this->tile_fmt332_ = (raw_w & 0x8000) != 0;   // high bit of w = 8-bit RGB332 payload
        this->tile_w_ = raw_w & 0x7FFF;
        this->tile_h_ = (this->stream_hdr_[6] << 8) | this->stream_hdr_[7];
        if (this->tile_w_ == 0 || this->tile_h_ == 0)
          continue;  // heartbeat / keepalive — no pixels follow
        uint32_t px = (uint32_t) this->tile_w_ * this->tile_h_;
        this->tile_need_ = px * (this->tile_fmt332_ ? 1 : 2);
        this->tile_have_ = 0;
        // Reject bad geometry but stay byte-synced by draining the payload.
        this->tile_skip_ = (px > STREAM_TILE_MAX_PIXELS) ||
                           (this->tile_x_ + this->tile_w_ > this->get_width_internal()) ||
                           (this->tile_y_ + this->tile_h_ > this->get_height_internal());
      }
    } else {
      // Reading the tile's pixel payload into stream_buf_ (or draining if skip).
      uint32_t left = this->tile_need_ - this->tile_have_;
      uint32_t want = left < budget ? left : budget;
      uint8_t scratch[256];
      uint8_t *dst = this->tile_skip_ ? scratch : (this->stream_buf_ + this->tile_have_);
      if (want > sizeof(scratch) && this->tile_skip_)
        want = sizeof(scratch);
      int n = this->stream_client_.read(dst, want);
      if (n <= 0)
        break;
      budget -= n;
      this->tile_have_ += n;
      this->last_stream_ms_ = millis();
      if (this->tile_have_ >= this->tile_need_) {
        if (!this->tile_skip_)
          this->stream_blit_tile_(this->tile_x_, this->tile_y_, this->tile_w_, this->tile_h_, this->stream_buf_,
                                  this->tile_need_, this->tile_fmt332_);
        this->tile_need_ = 0;
        this->tile_have_ = 0;
        this->tile_skip_ = false;
      }
    }
  }
  App.feed_wdt();
#endif
}

// Write one already-buffered tile straight to panel GRAM (mirrors write_display_data).
void ST7789V::stream_blit_tile_(uint16_t x, uint16_t y, uint16_t w, uint16_t h, const uint8_t *data, size_t len,
                                bool fmt332) {
  uint16_t x1 = this->offset_height_ + x;
  uint16_t x2 = x1 + w - 1;
  uint16_t y1 = this->offset_width_ + y;
  uint16_t y2 = y1 + h - 1;

  this->enable();
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_CASET);
  this->dc_pin_->digital_write(true);
  this->write_addr_(x1, x2);
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_RASET);
  this->dc_pin_->digital_write(true);
  this->write_addr_(y1, y2);
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_RAMWR);
  this->dc_pin_->digital_write(true);
  if (!fmt332) {
    this->write_array(data, len);                     // already RGB565 big-endian
  } else {
    uint8_t tmp[256];                                 // expand RGB332 -> RGB565 on the fly
    size_t ti = 0;
    for (size_t i = 0; i < len; i++) {
      uint16_t c = this->lut332_[data[i]];
      tmp[ti++] = c >> 8;
      tmp[ti++] = c & 0xFF;
      if (ti == sizeof(tmp)) {
        this->write_array(tmp, ti);
        ti = 0;
      }
    }
    if (ti)
      this->write_array(tmp, ti);
  }
  this->disable();
}

void ST7789V::fill(Color color) {
  if (this->eightbitcolor_) {
    auto color332 = display::ColorUtil::color_to_332(color);
    memset(this->buffer_, color332, this->buffer_fragment_length_pixels_);
  } else {
    auto color565 = display::ColorUtil::color_to_565(color);
    uint16_t *buff = (uint16_t *) this->buffer_;
    for (unsigned pos = 0; pos < this->buffer_fragment_length_pixels_; pos++)
      *buff++ = color565;
  }
}

void ST7789V::set_model_str(const char *model_str) { this->model_str_ = model_str; }

void ST7789V::write_display_data() {
  uint16_t x1 = this->offset_height_;
  uint16_t x2 = x1 + get_width_internal() - 1;
  uint16_t y1 = this->offset_width_ + this->current_fragment_offset_pixels_ / get_width_internal();
  uint16_t y2 = y1 + get_height_internal() - 1;

  this->enable();

  // set column(x) address
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_CASET);
  this->dc_pin_->digital_write(true);
  this->write_addr_(x1, x2);
  // set page(y) address
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_RASET);
  this->dc_pin_->digital_write(true);
  this->write_addr_(y1, y2);
  // write display memory
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_RAMWR);
  this->dc_pin_->digital_write(true);

  if (this->eightbitcolor_) {
    uint8_t temp_buffer[TEMP_BUFFER_SIZE];
    size_t temp_index = 0;
    for (int line = 0; line < this->get_buffer_length_(); line = line + this->get_width_internal()) {
      for (int index = 0; index < this->get_width_internal(); ++index) {
        auto color = display::ColorUtil::color_to_565(
            display::ColorUtil::to_color(this->buffer_[index + line], display::ColorOrder::COLOR_ORDER_RGB,
                                         display::ColorBitness::COLOR_BITNESS_332, true));
        temp_buffer[temp_index++] = (uint8_t) (color >> 8);
        temp_buffer[temp_index++] = (uint8_t) color;
        if (temp_index == TEMP_BUFFER_SIZE) {
          this->write_array(temp_buffer, TEMP_BUFFER_SIZE);
          temp_index = 0;
        }
      }
    }
    if (temp_index != 0)
      this->write_array(temp_buffer, temp_index);
  } else {
    this->write_array(this->buffer_, this->get_buffer_length_());
  }

  this->disable();
}

void ST7789V::init_reset_() {
  if (this->reset_pin_ != nullptr) {
    this->reset_pin_->setup();
    this->reset_pin_->digital_write(true);
    delay(1);
    // Trigger Reset
    this->reset_pin_->digital_write(false);
    delay(1);
    // Wake up
    this->reset_pin_->digital_write(true);
    delay(5);
  }
}

void ST7789V::backlight_(bool onoff) {
  if (this->backlight_pin_ != nullptr) {
    this->backlight_pin_->setup();
    this->backlight_pin_->digital_write(onoff);
  }
}

void ST7789V::write_command_(uint8_t value) {
  this->enable();
  this->dc_pin_->digital_write(false);
  this->write_byte(value);
  this->dc_pin_->digital_write(true);
  this->disable();
}

void ST7789V::write_data_(uint8_t value) {
  this->dc_pin_->digital_write(true);
  this->enable();
  this->write_byte(value);
  this->disable();
}

void ST7789V::write_addr_(uint16_t addr1, uint16_t addr2) {
  static uint8_t byte[4];
  byte[0] = (addr1 >> 8) & 0xFF;
  byte[1] = addr1 & 0xFF;
  byte[2] = (addr2 >> 8) & 0xFF;
  byte[3] = addr2 & 0xFF;

  this->dc_pin_->digital_write(true);
  this->write_array(byte, 4);
}

void ST7789V::write_color_(uint16_t color, uint16_t size) {
  static uint8_t byte[1024];
  int index = 0;
  for (int i = 0; i < size; i++) {
    byte[index++] = (color >> 8) & 0xFF;
    byte[index++] = color & 0xFF;
  }

  this->dc_pin_->digital_write(true);
  write_array(byte, size * 2);
}

size_t ST7789V::get_buffer_length_() {
  if (this->eightbitcolor_)
    return this->buffer_fragment_length_pixels_;
  return this->buffer_fragment_length_pixels_ * 2;
}

// Draw a filled rectangle
// x1: Start X coordinate
// y1: Start Y coordinate
// x2: End X coordinate
// y2: End Y coordinate
// color: color
void ST7789V::draw_filled_rect_(uint16_t x1, uint16_t y1, uint16_t x2, uint16_t y2, uint16_t color) {
  this->enable();
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_CASET);  // set column(x) address
  this->dc_pin_->digital_write(true);
  this->write_addr_(x1, x2);

  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_RASET);  // set Page(y) address
  this->dc_pin_->digital_write(true);
  this->write_addr_(y1, y2);
  this->dc_pin_->digital_write(false);
  this->write_byte(ST7789_RAMWR);  // begin a write to memory
  this->dc_pin_->digital_write(true);
  for (int i = x1; i <= x2; i++) {
    uint16_t size = y2 - y1 + 1;
    this->write_color_(color, size);
  }
  this->disable();
}

void HOT ST7789V::draw_absolute_pixel_internal(int x, int y, Color color) {
  if (x >= this->get_width_internal() || x < 0 || y >= this->get_height_internal() || y < 0)
    return;

  uint32_t pos = (x + y * this->get_width_internal());
  if (pos < this->current_fragment_offset_pixels_)
    return;
  pos -= this->current_fragment_offset_pixels_;
  if (pos >= this->buffer_fragment_length_pixels_)
    return;

  if (this->eightbitcolor_) {
    auto color332 = display::ColorUtil::color_to_332(color);
    this->buffer_[pos] = color332;
  } else {
    auto color565 = display::ColorUtil::color_to_565(color);
    this->buffer_[2 * pos + 0] = (color565 >> 8) & 0xff;
    this->buffer_[2 * pos + 1] = color565 & 0xff;
  }
}

}  // namespace st7789v
}  // namespace esphome
