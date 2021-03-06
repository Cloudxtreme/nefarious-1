import os
import logging
from django.contrib.auth.models import User
from django.utils.decorators import method_decorator
from django.views.decorators.cache import cache_page
from rest_framework import viewsets, views
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAdminUser

from nefarious.api.mixins import UserReferenceViewSetMixin, BlacklistAndRetryMixin
from nefarious.quality import PROFILES
from nefarious.transmission import get_transmission_client
from nefarious.tmdb import get_tmdb_client
from nefarious.api.serializers import (
    NefariousSettingsSerializer, WatchTVEpisodeSerializer, WatchTVShowSerializer,
    UserSerializer, WatchMovieSerializer, NefariousPartialSettingsSerializer,
    TransmissionTorrentSerializer, WatchTVSeasonSerializer)
from nefarious.models import NefariousSettings, WatchTVEpisode, WatchTVShow, WatchMovie, WatchTVSeason
from nefarious.search import MEDIA_TYPE_MOVIE, MEDIA_TYPE_TV, SearchTorrents
from nefarious.tasks import watch_tv_episode_task, watch_tv_show_season_task, watch_movie_task
from nefarious.utils import (
    trace_torrent_url, swap_jackett_host, is_magnet_url,
    verify_settings_jackett, verify_settings_transmission, verify_settings_tmdb,
    fetch_jackett_indexers)

CACHE_MINUTE = 60
CACHE_HOUR = CACHE_MINUTE * 60
CACHE_HALF_DAY = CACHE_HOUR * 12
CACHE_DAY = CACHE_HALF_DAY * 2
CACHE_WEEK = CACHE_DAY * 7


class WatchMovieViewSet(BlacklistAndRetryMixin, UserReferenceViewSetMixin, viewsets.ModelViewSet):
    queryset = WatchMovie.objects.all()
    serializer_class = WatchMovieSerializer
    filter_fields = ('collected',)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        # create a task to download the movie
        watch_movie_task.delay(serializer.instance.id)

    def perform_update(self, serializer):
        super().perform_update(serializer)
        # create a task to download the movie
        watch_movie_task.delay(serializer.instance.id)

    def _watch_media_task(self, watch_media_id: int):
        watch_movie_task.delay(watch_media_id)


class WatchTVShowViewSet(UserReferenceViewSetMixin, viewsets.ModelViewSet):
    queryset = WatchTVShow.objects.all()
    serializer_class = WatchTVShowSerializer

    @action(methods=['post'], detail=True, url_path='entire-season')
    def watch_entire_season(self, request, pk):
        watch_tv_show = self.get_object()  # type: WatchTVShow
        data = request.query_params or request.data

        if 'season_number' not in data:
            raise ValidationError({'season_number': ['This field is required']})

        watch_tv_season, was_created = WatchTVSeason.objects.get_or_create(
            user=request.user,
            watch_tv_show=watch_tv_show,
            season_number=data['season_number'],
        )

        if was_created:
            watch_tv_season.save()

        # create a task to download the whole season
        watch_tv_show_season_task.delay(watch_tv_season.id)

        return Response(
            WatchTVSeasonSerializer(watch_tv_season).data)


class WatchTVSeasonViewSet(BlacklistAndRetryMixin, UserReferenceViewSetMixin, viewsets.ModelViewSet):
    queryset = WatchTVSeason.objects.all()
    serializer_class = WatchTVSeasonSerializer
    filter_fields = ('collected',)

    def _watch_media_task(self, watch_media_id: int):
        watch_tv_show_season_task.delay(watch_media_id)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        # create a task to download the episode
        watch_tv_show_season_task.delay(serializer.instance.id)


class WatchTVEpisodeViewSet(BlacklistAndRetryMixin, UserReferenceViewSetMixin, viewsets.ModelViewSet):
    queryset = WatchTVEpisode.objects.all()
    serializer_class = WatchTVEpisodeSerializer
    filter_fields = ('collected',)

    def _watch_media_task(self, watch_media_id: int):
        watch_tv_episode_task.delay(watch_media_id)

    def perform_create(self, serializer):
        super().perform_create(serializer)
        # create a task to download the episode
        watch_tv_episode_task.delay(serializer.instance.id)


class SettingsViewSet(viewsets.ModelViewSet):
    queryset = NefariousSettings.objects.all()

    @action(methods=['get'], detail=True, permission_classes=(IsAdminUser,))
    def verify(self, request, pk):
        nefarious_settings = self.queryset.get(id=pk)
        try:
            verify_settings_jackett(nefarious_settings)
            verify_settings_tmdb(nefarious_settings)
            verify_settings_transmission(nefarious_settings)
        except Exception as e:
            raise ValidationError(str(e))
        return Response()

    @action(methods=['get'], detail=True, url_path='verify-jackett-indexers', permission_classes=(IsAdminUser,))
    def verify_jackett_indexers(self, request, pk):
        nefarious_settings = self.queryset.get(id=pk)
        try:
            results = verify_settings_jackett(nefarious_settings)
        except Exception as e:
            raise ValidationError(str(e))
        return Response(results.get('Indexers'))

    def get_serializer_class(self):
        if self.request.user.is_staff:
            return NefariousSettingsSerializer
        return NefariousPartialSettingsSerializer

    @action(methods=['get'], detail=False, url_path='configured-indexers', permission_classes=(IsAdminUser,))
    def configured_indexers(self, request):
        nefarious_settings = NefariousSettings.get()
        return Response(fetch_jackett_indexers(nefarious_settings))


class UserViewSet(viewsets.ModelViewSet):
    permission_classes = (IsAdminUser,)
    queryset = User.objects.all()
    serializer_class = UserSerializer


class CurrentUserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    def get_queryset(self):
        return self.queryset.filter(username=self.request.user.username)


class MediaDetailView(views.APIView):

    @method_decorator(cache_page(CACHE_DAY))
    def get(self, request, media_type, media_id):
        nefarious_settings = NefariousSettings.get()
        tmdb = get_tmdb_client(nefarious_settings)

        if media_type == MEDIA_TYPE_MOVIE:
            movie = tmdb.Movies(media_id)
            response = movie.info()
        else:
            tv = tmdb.TV(media_id)
            response = tv.info()
            # omit season "0" -- special episodes
            response['seasons'] = [season for season in response['seasons'] if season['season_number'] > 0]
            for season in response['seasons']:
                seasons_request = tmdb.TV_Seasons(response['id'], season['season_number'])
                seasons = seasons_request.info()
                season['episodes'] = seasons['episodes']

        return Response(response)


class SearchMediaView(views.APIView):

    @method_decorator(cache_page(CACHE_DAY))
    def get(self, request):
        media_type = request.query_params.get('media_type', MEDIA_TYPE_TV)
        assert media_type in [MEDIA_TYPE_TV, MEDIA_TYPE_MOVIE]

        nefarious_settings = NefariousSettings.get()

        # prepare query
        tmdb = get_tmdb_client(nefarious_settings)
        query = request.query_params.get('q')

        params = {
            'query': query,
        }

        # search for media
        search = tmdb.Search()

        if media_type == MEDIA_TYPE_MOVIE:
            results = search.movie(**params)
        else:
            results = search.tv(**params)

        return Response(results)


class SearchTorrentsView(views.APIView):

    @method_decorator(cache_page(CACHE_HALF_DAY))
    def get(self, request):
        query = request.query_params.get('q')
        media_type = request.query_params.get('media_type', MEDIA_TYPE_MOVIE)
        search = SearchTorrents(media_type, query)
        if not search.ok:
            return Response({'error': search.error_content}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response(search.results)


class DownloadTorrentsView(views.APIView):
    permission_classes = (IsAdminUser,)

    def post(self, request):
        nefarious_settings = NefariousSettings.get()
        torrent = request.data.get('torrent')
        media_type = request.data.get('media_type', MEDIA_TYPE_TV)
        if not is_magnet_url(torrent):
            torrent = swap_jackett_host(torrent, nefarious_settings)

        if not torrent:
            return Response({'success': False, 'error': 'Missing torrent link'})

        try:
            torrent = trace_torrent_url(torrent)
        except Exception as e:
            return Response({'success': False, 'error': 'An unknown error occurred', 'error_detail': str(e)})

        logging.info('adding torrent: {}'.format(torrent))

        # add torrent
        transmission_client = get_transmission_client(nefarious_settings)
        transmission_session = transmission_client.session_stats()

        if media_type == MEDIA_TYPE_MOVIE:
            download_dir = os.path.join(
                transmission_session.download_dir, nefarious_settings.transmission_movie_download_dir.lstrip('/'))
        else:
            download_dir = os.path.join(
                transmission_session.download_dir, nefarious_settings.transmission_tv_download_dir.lstrip('/'))

        transmission_client.add_torrent(
            torrent,
            paused=True,
            download_dir=download_dir,
        )
        
        return Response({'success': True})


class CurrentTorrentsView(views.APIView):

    def get(self, request):
        nefarious_settings = NefariousSettings.get()
        transmission_client = get_transmission_client(nefarious_settings)

        watch_movies = request.query_params.getlist('watch_movies')
        watch_tv_episodes = request.query_params.getlist('watch_tv_episodes')
        watch_tv_seasons = request.query_params.getlist('watch_tv_seasons')

        querysets = []
        torrents = []
        torrent_hashes = []

        # movies
        if watch_movies:
            querysets.append(
                WatchMovie.objects.filter(id__in=watch_movies))
        # tv episodes
        if watch_tv_episodes:
            querysets.append(
                WatchTVEpisode.objects.filter(id__in=watch_tv_episodes))
        # tv seasons
        if watch_tv_seasons:
            querysets.append(
                WatchTVSeason.objects.filter(id__in=watch_tv_seasons))

        for query in querysets:
            torrent_hashes += [media.transmission_torrent_hash for media in query if media.transmission_torrent_hash]

        for torrent_hash in torrent_hashes:

            try:
                torrent = transmission_client.get_torrent(torrent_hash)
                torrents.append(torrent)
            except (KeyError, ValueError):  # torrent no longer exists or was invalid
                continue
            except Exception as e:
                logging.error(str(e))
                raise e

        return Response(TransmissionTorrentSerializer(torrents, many=True).data)


class DiscoverMediaView(views.APIView):

    @method_decorator(cache_page(CACHE_WEEK))
    def get(self, request, media_type):
        assert media_type in [MEDIA_TYPE_TV, MEDIA_TYPE_MOVIE]

        nefarious_settings = NefariousSettings.get()

        # prepare query
        tmdb = get_tmdb_client(nefarious_settings)
        args = request.query_params
        discover = tmdb.Discover()

        if media_type == MEDIA_TYPE_MOVIE:
            results = discover.movie(**args)
        else:
            results = discover.tv(**args)

        return Response(results)


class GenresView(views.APIView):

    @method_decorator(cache_page(CACHE_WEEK))
    def get(self, request, media_type):
        assert media_type in [MEDIA_TYPE_TV, MEDIA_TYPE_MOVIE]

        nefarious_settings = NefariousSettings.get()

        # prepare query
        tmdb = get_tmdb_client(nefarious_settings)
        args = request.query_params
        genres = tmdb.Genres()

        if media_type == MEDIA_TYPE_MOVIE:
            results = genres.movie_list(**args)
        else:
            results = genres.tv_list(**args)

        return Response(results)


class QualityProfilesView(views.APIView):

    def get(self, request):
        return Response({'profiles': [p.name for p in PROFILES]})
