import { Component, OnInit } from '@angular/core';
import { ActivatedRoute } from '@angular/router';
import { ApiService } from '../api.service';
import { ToastrService } from 'ngx-toastr';
import * as _ from 'lodash';
import { forkJoin, Observable } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';


@Component({
  selector: 'app-media-tv',
  templateUrl: './media-t-v.component.html',
  styleUrls: ['./media-t-v.component.css']
})
export class MediaTVComponent implements OnInit {
  public result: any;
  public watchEpisodesOptions: {
    [param: number]: boolean,
  };
  public isLoading = true;
  public isSaving = false;

  constructor(
    private route: ActivatedRoute,
    private apiService: ApiService,
    private toastr: ToastrService,
    ) {
  }

  ngOnInit() {
    const routeParams = this.route.snapshot.params;
    this.apiService.searchMediaDetail(this.apiService.SEARCH_MEDIA_TYPE_TV, routeParams.id).subscribe(
      (data) => {
        this.result = data;
        this._buildWatchOptions();
        this.isLoading = false;
      },
      (error) => {
        this.toastr.error('An unknown error occurred');
      }
    );
  }

  public submitForSeason(seasonNumber: number) {

    // watch show if not already
    if (!this.isWatchingShow()) {
      console.log('not already watching show %s', this.result.id);
      this._watchShow().subscribe(
        (data) => {
          this._watchEpisodesForSeason(seasonNumber);
        },
      );
    } else {
      this._watchEpisodesForSeason(seasonNumber);
    }
  }

  public mediaPosterURL(result) {
    return `${this.apiService.settings.tmdb_configuration.images.base_url}/original/${result.poster_path}`;
  }

  public watchAllSeasons() {

    if (!this.isWatchingShow()) {
      console.log('not yet watching show %s', this.result.name);
      this._watchShow().subscribe(
        (data) => {
          this.watchAllSeasons();
        }
      );
    } else {
        for (const season of this.result.seasons) {
          this.watchEntireSeason(season);
        }
    }
  }

  public watchEntireSeason(season) {

    this.isSaving = true;

    if (!this.isWatchingShow()) {
      console.log('not yet watching show %s', this.result.name);
      this._watchShow().subscribe(
        (data) => {
          this.watchEntireSeason(season);
        }
      );
    } else {

        const watchTvShow = this._getWatchShow();

        this.apiService.watchTVSeason(watchTvShow.id, season.season_number).subscribe(
          (data) => {
            this.isSaving = false;
            this.toastr.success(`Watching season ${season.season_number}`);
            this._buildWatchOptions();
          },
          (error) => {
            this.isSaving = false;
            this.toastr.error('An unknown error occurred');
            console.log(error);
          }
        );
    }
  }

  public stopWatchingShow() {
    const watchShow = this._getWatchShow();
    if (watchShow) {
      this.apiService.unWatchTVShow(watchShow.id).subscribe(
        (data) => {
          this.toastr.success('Stop watching show');
        },
        (error) => {
          this.toastr.error('An unknown error occurred');
        }
      );
    }
  }

  public getWatchMedia() {
    const watching = [];
    for (const season of this.result.seasons) {
      const watchSeason = this._getWatchSeason(season.season_number);
      if (watchSeason) {
        watching.push(watchSeason);
      }
      for (const episode of season.episodes) {
        const watchEpisode = this._getEpisodeWatch(episode.id);
        if (watchEpisode) {
          watching.push(watchEpisode);
        }
      }
    }
    return watching;
  }

  public isWatchingSeason(seasonNumber: number) {
    const watchSeason = this._getWatchSeason(seasonNumber);
    return Boolean(watchSeason);
  }

  public stopWatchingEntireSeason(season: any) {
    const watchSeason = this._getWatchSeason(season.season_number);
    if (watchSeason) {
        this.apiService.unWatchTVSeason(watchSeason.id).subscribe(
          (data) => {
            this.toastr.success(`Stop watching ${this.result.name} - Season ${watchSeason.season_number}`)
          },
          (error) => {
            console.error(error);
            this.toastr.error('An unknown error occurred');
          }
        );
    }
  }

  public isWatchingShow() {
    return Boolean(this._getWatchShow());
  }

  protected _watchShow(): Observable<any> {
    return this.apiService.watchTVShow(this.result.id, this.result.name, this.mediaPosterURL(this.result)).pipe(
      tap((data) => {
        this.toastr.success(`Watching show ${data.name}`);
      }),
      catchError((error) => {
        this.toastr.error('An unknown error occurred');
        throw error;
      }),
    );
  }

  protected _getWatchSeason(seasonNumber: number) {
    const watchShow = this._getWatchShow();
    if (watchShow) {
      return _.find(this.apiService.watchTVSeasons, (watchSeason) => {
        return watchSeason.watch_tv_show === watchShow.id && watchSeason.season_number === seasonNumber;
      });
    }
    return null;
  }

  protected _buildWatchOptions() {
    const watchingOptions: any = {};
    for (const season of this.result.seasons) {
      for (const episode of season.episodes) {
        watchingOptions[episode.id] = this._isWatchingEpisode(episode.id) || this.isWatchingSeason(season.season_number);
      }
    }
    this.watchEpisodesOptions = watchingOptions;
  }

  protected _isWatchingEpisode(episodeId): Boolean {
    return Boolean(_.find(this.apiService.watchTVEpisodes, (watching) => {
      return watching.tmdb_episode_id === episodeId;
    }));
  }

  protected _getEpisodeWatch(episodeId) {
    return _.find(this.apiService.watchTVEpisodes, (watch) => {
      return watch.tmdb_episode_id === episodeId;
    });
  }

  protected _getSeasonFromEpisodeId(episodeId) {
    let result;
    _.each(this.result.seasons, (season) => {
      _.each(season.episodes, (episode) => {
        if (episode.id === episodeId) {
          result = season;
          return;
        }
      });
    });
    return result;
  }

  protected _getEpisode(episodeId) {
    let result = null;
    _.each(this.result.seasons, (season) => {
      _.each(season.episodes, (episode) => {
        if (episode.id === episodeId) {
          result = episode;
          return;
        }
      });
    });
    return result;
  }

  protected _getWatchShow() {
    return _.find(this.apiService.watchTVShows, (watchShow) => {
      return watchShow.tmdb_show_id === this.result.id;
    });
  }

  protected _watchEpisodesForSeason(seasonNumber: number) {

    const observables = [];

    _.forOwn(this.watchEpisodesOptions, (shouldWatch: boolean, episodeIdString: string) => {
      const episodeId = Number(episodeIdString);
      const episode = this._getEpisode(episodeId);

      // requested to watch
      if (shouldWatch) {
        // make sure the episode is for the supplied season and they're not already watching it
        if (seasonNumber === episode.season_number && !this._isWatchingEpisode(episodeId)) {
          const season = this._getSeasonFromEpisodeId(episodeId);
          const watchShow = this._getWatchShow();
          if (episode && season && watchShow) {
            observables.push(
              this.apiService.watchTVEpisode(
                watchShow.id, Number(episodeId), episode.season_number, episode.episode_number));
          } else {
            console.log('ERROR: episode %s not found in results', episodeId);
          }
        }
      } else { // stop watching
        if (this._isWatchingEpisode(episodeId)) {
          const watch = this._getEpisodeWatch(episodeId);
          observables.push(this.apiService.unWatchTVEpisode(watch.id));
        }
      }
    });

    // run any updates
    if (observables.length) {
      this.isSaving = true;
      forkJoin(observables).subscribe(
        (data) => {
          this.isSaving = false;
          this.toastr.success('Saved');
        },
        (error) => {
          this.isSaving = false;
          this.toastr.error('An unknown error occurred');
        },
        () => {
          this._buildWatchOptions();
        }
      );
    }

  }
}
